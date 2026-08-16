"""
Regression guard for the NIW / patent demonstration surface.

Asserts that the scenario, pricing, and benchmark endpoints still return the
numbers recorded in tests/golden/. These endpoints back USPTO App. No.
19/641,446 and the pending NIW petition; they are reviewed by people outside
this repo and must not move as a side effect of Phase 1 agent work.

If a test here fails, the question is not "how do I update the golden file" --
it is "why did the demonstration numbers change".
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, List

import pytest

from tests.golden_spec import capture, iter_cases

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# Absolute tolerance for float comparison.
#
# Goldens are captured on darwin/arm64 and asserted in CI on linux/amd64.
# PageRank and betweenness centrality accumulate floats in an order that can
# differ across BLAS builds. The engine rounds most outputs to 4dp, so real
# regressions are far larger than this, while a value sitting exactly on a
# rounding boundary can flip its last digit for no meaningful reason.
FLOAT_TOL = 1e-6


def _fmt(path: List[str]) -> str:
    return "response" + "".join(path) if path else "response"


def _compare(actual: Any, expected: Any, path: List[str], diffs: List[str]) -> None:
    """Recursively compare, collecting readable path-anchored differences."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            diffs.append(f"{_fmt(path)}: expected object, got {type(actual).__name__}")
            return
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                diffs.append(f"{_fmt(path)}[{key!r}]: MISSING from response")
            elif key not in expected:
                diffs.append(f"{_fmt(path)}[{key!r}]: UNEXPECTED in response")
            else:
                _compare(actual[key], expected[key], path + [f"[{key!r}]"], diffs)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            diffs.append(f"{_fmt(path)}: expected list, got {type(actual).__name__}")
            return
        if len(actual) != len(expected):
            diffs.append(
                f"{_fmt(path)}: length {len(actual)} != expected {len(expected)}"
            )
            return
        for i, (a, e) in enumerate(zip(actual, expected)):
            _compare(a, e, path + [f"[{i}]"], diffs)
        return

    # bool must be checked before the numeric branch -- bool is an int in Python
    # and True would otherwise compare equal to 1.
    if isinstance(expected, bool) or isinstance(actual, bool):
        if actual is not expected:
            diffs.append(f"{_fmt(path)}: {actual!r} != expected {expected!r}")
        return

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if not math.isclose(float(actual), float(expected), abs_tol=FLOAT_TOL):
            delta = float(actual) - float(expected)
            diffs.append(
                f"{_fmt(path)}: {actual!r} != expected {expected!r}  (delta {delta:+.6g})"
            )
        return

    if actual != expected:
        diffs.append(f"{_fmt(path)}: {actual!r} != expected {expected!r}")


CASES = list(iter_cases())


@pytest.mark.parametrize("name,runner", CASES, ids=[n for n, _ in CASES])
def test_niw_surface_matches_golden(name, runner):
    golden_path = GOLDEN_DIR / f"{name}.json"
    if not golden_path.exists():
        pytest.fail(
            f"No golden file for case {name!r}.\n"
            f"Expected: {golden_path}\n"
            "Generate it with:  .venv/bin/python -m tests.regen_golden"
        )

    expected = json.loads(golden_path.read_text())
    actual = capture(name, runner)

    diffs: List[str] = []
    _compare(actual, expected, [], diffs)

    if diffs:
        shown = diffs[:25]
        raise AssertionError(
            f"NIW demonstration surface changed for case {name!r} "
            f"({len(diffs)} difference(s)).\n\n"
            + "\n".join(f"  {d}" for d in shown)
            + (f"\n  ... and {len(diffs) - 25} more" if len(diffs) > 25 else "")
            + "\n\nThese endpoints are USPTO/NIW-facing. Do NOT regenerate the\n"
            "golden file to silence this -- establish why the numbers moved."
        )


def test_every_golden_file_has_a_case():
    """
    A golden file with no corresponding case is dead weight that no longer
    guards anything -- catch it rather than let it rot in the tree.
    """
    on_disk = {p.stem for p in GOLDEN_DIR.glob("*.json")}
    in_spec = {name for name, _ in CASES}
    orphaned = on_disk - in_spec
    assert not orphaned, (
        f"Golden files with no matching case: {sorted(orphaned)}. "
        "Remove them or restore the case in golden_spec.py."
    )
