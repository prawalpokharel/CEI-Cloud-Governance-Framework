"""
Summarize how the NIW surface differs from the committed golden files.

Raw JSON diffs across 32 golden files are unreadable. This reports the
figures a reviewer actually looks at -- classifications, recommendations,
savings, weights, oscillation state -- so a change to the demonstration
numbers can be judged before it is committed.

    .venv/bin/python -m tests.compare_surface
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.golden_spec import SCENARIO_IDS, capture, iter_cases  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _load(name: str):
    p = GOLDEN_DIR / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _analysis(payload):
    if payload is None:
        return None
    return payload.get("analysis", payload)


def _fmt_counter(c: Counter) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(c.items())) or "-"


def main() -> int:
    cases = dict(iter_cases())

    print("=" * 78)
    print("NIW SURFACE — committed golden (BEFORE) vs current code (AFTER)")
    print("=" * 78)

    for sid in SCENARIO_IDS:
        before = _analysis(_load(f"analyze_{sid}"))
        after = _analysis(capture(f"analyze_{sid}", cases[f"analyze_{sid}"]))
        if before is None:
            print(f"\n{sid}: no golden on disk")
            continue

        print(f"\n{'-' * 78}\n{sid}\n{'-' * 78}")

        for label, data in (("BEFORE", before), ("AFTER ", after)):
            nodes = data["nodes"]
            cls = Counter(n["classification"] for n in nodes)
            rec = Counter(n["recommendation"] for n in nodes)
            act = Counter(n.get("action_type") for n in nodes)
            osc = data["oscillation_status"]
            w = data["weights"]
            print(
                f"  {label}  weights a={w['alpha']:.3f} b={w['beta']:.3f} g={w['gamma']:.3f}"
                f"   savings=${data['total_potential_savings']:.2f}"
            )
            print(f"          class:  {_fmt_counter(cls)}")
            print(f"          rec:    {_fmt_counter(rec)}")
            print(f"          action: {_fmt_counter(act)}")
            print(
                f"          osc:    {osc['oscillating_node_count']}/{osc['total_nodes']}"
                f" flagged, suppression={osc['suppression_active']},"
                f" score={osc['system_oscillation_score']}"
            )

        # Per-node CEI movement
        b_by = {n["node_id"]: n for n in before["nodes"]}
        moved = []
        for n in after["nodes"]:
            b = b_by.get(n["node_id"])
            if not b:
                continue
            d = n["cei_score"] - b["cei_score"]
            if abs(d) >= 0.01 or n["classification"] != b["classification"]:
                moved.append((abs(d), n["node_id"], b, n))
        moved.sort(reverse=True)
        if moved:
            print(f"\n          largest CEI movements ({len(moved)} node(s) moved):")
            for _, nid, b, a in moved[:6]:
                flag = ""
                if b["classification"] != a["classification"]:
                    flag = f"   {b['classification']} -> {a['classification']}"
                print(
                    f"            {nid:28s} {b['cei_score']:.4f} -> {a['cei_score']:.4f}"
                    f"  ({a['cei_score'] - b['cei_score']:+.4f}){flag}"
                )

        # Entropy is the term the telemetry path feeds; report it directly.
        for label, data in (("BEFORE", before), ("AFTER ", after)):
            ents = [n["entropy"] for n in data["nodes"]]
            print(
                f"          {label} entropy: min={min(ents):.4f} "
                f"max={max(ents):.4f} spread={max(ents) - min(ents):.4f}"
            )

    # Savings endpoints
    print(f"\n{'=' * 78}\nPRICING / SAVINGS\n{'=' * 78}")
    for sid in SCENARIO_IDS:
        name = f"pricing_savings_{sid}"
        before = _load(name)
        after = capture(name, cases[name])
        if before is None:
            continue
        ba = Counter(r["action"] for r in before["node_recommendations"])
        aa = Counter(r["action"] for r in after["node_recommendations"])
        print(f"\n  {sid}")
        print(
            f"    BEFORE  monthly savings ${before['total_monthly_savings_usd']:>10,.2f}"
            f"   actions: {_fmt_counter(ba)}"
        )
        print(
            f"    AFTER   monthly savings ${after['total_monthly_savings_usd']:>10,.2f}"
            f"   actions: {_fmt_counter(aa)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
