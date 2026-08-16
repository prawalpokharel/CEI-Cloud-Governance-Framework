"""
Remediation policy engine.

Phase 7. Decides, for a given finding, what the system is permitted to do
about it without a human:

    auto_apply   act now, record it, do not ask
    open_pr      propose the change, a human merges
    alert_only   tell someone, change nothing
    blocked      governance forbids acting at all

The roadmap states the shape directly: "auto-fix patch versions, PR minor
versions, alert-only on major versions". This generalizes it, because version
semantics are only one of the inputs that should gate an automatic change.

## Why the gate is not just severity

The instinct is to automate the most urgent things first. That is backwards.
Urgency describes how much you want the change; it says nothing about how
safe the change is. The most urgent fix on the most critical workload is
exactly the change you least want applied unattended at 3am.

So the decision is governed by **blast radius and reversibility**, not
urgency:

* A patch bump on an isolated workload is safe to apply — small change,
  small blast radius, trivially revertible.
* The same patch bump on a workload half the cluster depends on is a pull
  request, because the cost of being wrong is paid by everything downstream.
* A major version bump is never automatic at any blast radius. Major means
  the maintainers are telling you it breaks.

## Nothing here executes

This module decides. Execution — opening the PR, applying the manifest —
lives behind an explicit, separately-gated integration. Keeping the decision
pure means it can be tested exhaustively and dry-run against real findings
before anything is wired to a credential that can change a customer's system.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any


class Action(str, enum.Enum):
    auto_apply = "auto_apply"
    open_pr = "open_pr"
    alert_only = "alert_only"
    blocked = "blocked"


class ChangeKind(str, enum.Enum):
    patch_bump = "patch_bump"
    minor_bump = "minor_bump"
    major_bump = "major_bump"
    base_image_rebase = "base_image_rebase"
    resource_rightsize = "resource_rightsize"
    replica_change = "replica_change"
    network_policy = "network_policy"
    unknown = "unknown"


# Above this, a workload has enough depending on it that an unattended change
# is not worth the saved minute. Deliberately lower than the "elevated" CEI
# band: the threshold for touching something automatically should be stricter
# than the threshold for calling it important.
AUTO_APPLY_CEI_CEILING = 0.45

from .constants import SYSTEM_NAMESPACES

# Nothing in these namespaces is ever changed automatically, whatever the
# change. Same set the NetworkPolicy generator excludes: two lists meaning the
# same thing drift, and the drift is silent.
PROTECTED_NAMESPACES = SYSTEM_NAMESPACES

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass
class PolicyDecision:
    action: Action
    change_kind: ChangeKind
    reason: str
    requires_approval: bool
    reversible: bool
    guardrails: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "change_kind": self.change_kind.value,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "reversible": self.reversible,
            "guardrails": self.guardrails,
        }


def classify_version_change(installed: str | None, fixed: str | None) -> ChangeKind:
    """
    Classify a version bump by semver distance.

    Unparseable versions return `unknown` rather than a guess. Distro package
    versions (`3.5.1-1+deb13u1`) and calendar versions do not follow semver,
    and treating an unrecognized format as a patch bump would authorize
    automatic application of a change nobody classified.
    """
    if not installed or not fixed:
        return ChangeKind.unknown

    left = _SEMVER.match(installed.strip())
    right = _SEMVER.match(fixed.strip())
    if not left or not right:
        return ChangeKind.unknown

    lmajor, lminor, lpatch = (int(x) if x else 0 for x in left.groups())
    rmajor, rminor, rpatch = (int(x) if x else 0 for x in right.groups())

    if rmajor != lmajor:
        return ChangeKind.major_bump
    if rminor != lminor:
        return ChangeKind.minor_bump
    if rpatch != lpatch:
        return ChangeKind.patch_bump
    return ChangeKind.unknown


def decide(
    *,
    change_kind: ChangeKind,
    cei_score: float | None,
    namespace: str,
    internet_reachable: bool = False,
    auto_apply_enabled: bool = False,
    has_tests: bool = False,
) -> PolicyDecision:
    """
    Decide what may be done about one finding.

    ``auto_apply_enabled`` defaults to False: automatic application is
    opt-in per customer, and a system that starts changing things because it
    was installed is a system nobody installs twice.
    """
    guardrails: list[str] = []
    cei = cei_score if cei_score is not None else 1.0  # unknown = treat as critical

    if namespace in PROTECTED_NAMESPACES:
        return PolicyDecision(
            action=Action.alert_only,
            change_kind=change_kind,
            reason=(
                f"{namespace} is a protected namespace. Changes to cluster "
                "infrastructure are never proposed automatically — a cluster "
                "that repairs its own control plane unattended can lock you "
                "out of it."
            ),
            requires_approval=True,
            reversible=False,
            guardrails=["protected_namespace"],
        )

    if change_kind == ChangeKind.major_bump:
        return PolicyDecision(
            action=Action.alert_only,
            change_kind=change_kind,
            reason=(
                "Major version bump. The maintainers have signalled a breaking "
                "change, so this needs a human to read the changelog — no "
                "amount of passing tests makes a major bump routine."
            ),
            requires_approval=True,
            reversible=True,
            guardrails=["major_version"],
        )

    if change_kind == ChangeKind.unknown:
        return PolicyDecision(
            action=Action.alert_only,
            change_kind=change_kind,
            reason=(
                "The version change could not be classified — distro and "
                "calendar versioning do not follow semver. Treating an "
                "unrecognized format as a patch would authorize a change "
                "nobody assessed."
            ),
            requires_approval=True,
            reversible=True,
            guardrails=["unclassified_version"],
        )

    # Changes that alter running capacity or connectivity are never applied
    # unattended in this phase, whatever their blast radius. They are the
    # changes most likely to cause an outage while looking safe in review.
    if change_kind in (
        ChangeKind.replica_change,
        ChangeKind.network_policy,
        ChangeKind.resource_rightsize,
    ):
        return PolicyDecision(
            action=Action.open_pr,
            change_kind=change_kind,
            reason=(
                "Changes to capacity or connectivity are proposed, never "
                "applied unattended. A NetworkPolicy derived from observed "
                "dependencies cannot see a dependency that was never observed."
            ),
            requires_approval=True,
            reversible=True,
            guardrails=["capacity_or_connectivity_change"],
        )

    if internet_reachable:
        guardrails.append("internet_reachable")
    if cei >= AUTO_APPLY_CEI_CEILING:
        guardrails.append("high_blast_radius")

    can_auto = (
        auto_apply_enabled
        and change_kind == ChangeKind.patch_bump
        and cei < AUTO_APPLY_CEI_CEILING
        and not internet_reachable
        and has_tests
    )

    if can_auto:
        return PolicyDecision(
            action=Action.auto_apply,
            change_kind=change_kind,
            reason=(
                f"Patch bump on a workload with limited blast radius "
                f"(CEI {cei:.2f}), covered by tests. Small change, small "
                "consequence if wrong, trivially revertible."
            ),
            requires_approval=False,
            reversible=True,
            guardrails=guardrails,
        )

    # Everything else that is actionable becomes a pull request. Explaining
    # which gate stopped it matters more than the verdict: "needs approval"
    # with no reason is how a policy engine becomes something people disable.
    if not auto_apply_enabled:
        reason = (
            "Automatic application is not enabled for this cluster, so the "
            "change is proposed for review."
        )
    elif change_kind != ChangeKind.patch_bump:
        reason = (
            "Minor version bump. Behaviour can change within a minor release, "
            "so this is proposed rather than applied."
        )
    elif cei >= AUTO_APPLY_CEI_CEILING:
        reason = (
            f"Blast radius too large for an unattended change "
            f"(CEI {cei:.2f} ≥ {AUTO_APPLY_CEI_CEILING}). Other workloads "
            "depend on this one, so the cost of being wrong is paid "
            "downstream."
        )
    elif internet_reachable:
        reason = (
            "Workload is reachable from outside the cluster. Changes to "
            "anything on the attack surface get a human."
        )
    elif not has_tests:
        reason = (
            "No test coverage detected for this component, so nothing would "
            "catch a bad change before it reached production."
        )
    else:
        reason = "Proposed for review."

    return PolicyDecision(
        action=Action.open_pr,
        change_kind=change_kind,
        reason=reason,
        requires_approval=True,
        reversible=True,
        guardrails=guardrails,
    )


def plan_remediation(
    prioritized_findings: list[dict],
    *,
    auto_apply_enabled: bool = False,
    workload_namespaces: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Turn ranked vulnerability findings into a remediation plan.

    Consumes the output of services.vulnerability.prioritize, so the plan
    inherits the CEI ordering: what gets fixed first is what puts most at
    risk, and what gets fixed automatically is what is safe to touch.
    """
    workload_namespaces = workload_namespaces or {}
    plan = []
    counts = {a.value: 0 for a in Action}

    for finding in prioritized_findings:
        keys = finding.get("workload_keys") or []
        namespace = "default"
        for key in keys:
            namespace = workload_namespaces.get(key) or key.split("/")[0]
            break

        change_kind = classify_version_change(
            finding.get("installed_version"), finding.get("fixed_version")
        )
        if not finding.get("fixed_version"):
            decision = PolicyDecision(
                action=Action.alert_only,
                change_kind=ChangeKind.unknown,
                reason=(
                    "No fixed version published. The response is to isolate "
                    "or replace the component, which is not a change this "
                    "system can propose."
                ),
                requires_approval=True,
                reversible=False,
                guardrails=["no_fix_available"],
            )
        else:
            decision = decide(
                change_kind=change_kind,
                cei_score=finding.get("cei_score"),
                namespace=namespace,
                internet_reachable=bool(finding.get("internet_reachable")),
                auto_apply_enabled=auto_apply_enabled,
                # Test coverage is not detectable until the Git integration
                # exists, so nothing qualifies for auto-apply yet. Stated
                # rather than defaulted to True, which would silently
                # authorize unattended changes.
                has_tests=False,
            )

        counts[decision.action.value] += 1
        plan.append({
            "vulnerability_id": finding.get("vulnerability_id"),
            "package": finding.get("package"),
            "installed_version": finding.get("installed_version"),
            "fixed_version": finding.get("fixed_version"),
            "image_reference": finding.get("image_reference"),
            "workload_keys": keys,
            "priority_score": finding.get("priority_score"),
            "decision": decision.to_dict(),
        })

    return {
        "summary": {
            "total": len(plan),
            "by_action": counts,
            "auto_apply_enabled": auto_apply_enabled,
            "note": (
                "Automation is gated on blast radius and reversibility, not "
                "urgency. The most urgent fix on the most critical workload "
                "is the change you least want applied unattended."
            ),
        },
        "plan": plan,
    }
