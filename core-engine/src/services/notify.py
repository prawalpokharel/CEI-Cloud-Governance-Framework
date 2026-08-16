"""
Outbound notifications: Slack alerts and report email.

## The alerting thesis

Roadmap: "Slack alerts filtered to high-CEI anomalies only — structural answer
to alert fatigue."

Alert fatigue is not caused by alerts being too verbose. It is caused by
alerts that do not distinguish between a crash loop in the payments database
and one in a scratch namespace. When every notification looks equally urgent,
people learn to ignore all of them, and the one that mattered is ignored too.

So the filter is structural rather than cosmetic: an alert fires only when the
affected workload's CEI clears a threshold, meaning something depends on it.
A crash loop nobody depends on is a dashboard entry, not an interruption.

## Delivery

Both channels are pluggable and default to a no-op that logs. Nothing here
requires an account to develop against, and a missing provider degrades to
"not delivered" rather than to an exception in a scheduled job.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any

log = logging.getLogger(__name__)

# Below this, something failing affects only itself. Above it, other workloads
# depend on it and a failure spreads. Chosen to sit just under the "moderate"
# classification boundary so genuinely peripheral workloads stay silent.
DEFAULT_CEI_ALERT_THRESHOLD = 0.45

# Even a high-CEI warning is rarely worth interrupting someone. Criticals are.
ALERTABLE_SEVERITIES = {"critical"}


class DeliveryError(Exception):
    pass


# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------

def select_alertable(
    findings: list[dict],
    cei_threshold: float = DEFAULT_CEI_ALERT_THRESHOLD,
) -> list[dict]:
    """
    Reduce health findings to the ones worth interrupting someone for.

    Two gates, both required: the finding is critical, AND the affected
    workload has enough depending on it that a failure propagates. Either gate
    alone reproduces the alert fatigue this exists to avoid -- severity alone
    pages on scratch namespaces, CEI alone pages on warnings.

    A finding with no CEI score (no workload attributed) is not alerted on. It
    cannot be assessed, and defaulting to "page someone" is how a channel gets
    muted.
    """
    alertable = []
    for finding in findings:
        if finding.get("severity") not in ALERTABLE_SEVERITIES:
            continue
        cei = finding.get("cei_score")
        if cei is None or cei < cei_threshold:
            continue
        alertable.append(finding)
    return alertable


def build_slack_message(
    cluster_name: str,
    findings: list[dict],
    dashboard_url: str | None = None,
) -> dict[str, Any]:
    """Compose a Slack Block Kit payload."""
    count = len(findings)
    header = (
        f"{count} critical issue{'s' if count != 1 else ''} in {cluster_name}"
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header},
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    "Filtered to workloads other services depend on. "
                    "Lower-impact issues are on the dashboard."
                ),
            }],
        },
    ]

    for finding in findings[:5]:
        cei = finding.get("cei_score")
        title = finding.get("title", "Issue")
        detail = (finding.get("detail") or "")[:280]
        namespace = finding.get("namespace", "?")

        context = f"_namespace `{namespace}`_"
        if cei is not None:
            context = f"_namespace `{namespace}` · CEI {cei:.2f}_"

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{title}*\n{detail}\n{context}",
            },
        })

    if count > 5:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_and {count - 5} more_"}
            ],
        })

    if dashboard_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Open dashboard"},
                "url": dashboard_url,
            }],
        })

    return {"text": header, "blocks": blocks}


def send_slack(payload: dict, webhook_url: str | None = None) -> bool:
    """
    POST to a Slack incoming webhook.

    Returns False rather than raising when no webhook is configured: a
    scheduled job should not fail because a customer has not connected Slack.
    """
    webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        log.info("No SLACK_WEBHOOK_URL configured; skipping Slack delivery")
        return False

    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status >= 300:
                raise DeliveryError(f"Slack returned HTTP {response.status}")
        return True
    except DeliveryError:
        raise
    except Exception as exc:
        raise DeliveryError(f"Slack delivery failed: {exc}") from exc


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

def send_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
    *,
    sender: str | None = None,
) -> bool:
    """
    Send a multipart email via SMTP.

    Multipart with a text alternative, not HTML alone: a single-part HTML mail
    is more likely to be filtered as spam, and some clients still render text
    only.

    Configuration is standard SMTP so any provider works (SES, SendGrid,
    Postmark, Resend all expose SMTP). Returns False when unconfigured rather
    than raising, so an unconfigured deployment degrades to "not sent".
    """
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        log.info("No SMTP_HOST configured; skipping email delivery to %s", to)
        return False

    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    sender = sender or os.environ.get(
        "SMTP_FROM", "reports@cloudoptimizer.app"
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        log.info("Report emailed to %s", to)
        return True
    except Exception as exc:
        raise DeliveryError(f"Email delivery failed: {exc}") from exc
