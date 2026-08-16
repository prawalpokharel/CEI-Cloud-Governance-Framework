"""
Regenerate the NIW golden files.

    cd core-engine && .venv/bin/python -m tests.regen_golden

Run this ONLY when a change to the demonstration numbers is intended and
reviewed. The whole point of the harness is that an unintended change shows
up as a failing test rather than a silently-updated file, so regenerating to
"make the tests pass" defeats it.

Every run prints a diff summary of which cases moved, so an accidental
regeneration is visible in the pull request.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow `python -m tests.regen_golden` from the core-engine directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.golden_spec import capture, iter_cases  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _write(name: str, payload) -> str:
    path = GOLDEN_DIR / f"{name}.json"
    new_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    if not path.exists():
        path.write_text(new_text)
        return "created"

    if path.read_text() == new_text:
        return "unchanged"

    path.write_text(new_text)
    return "CHANGED"


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    counts = {"created": 0, "unchanged": 0, "CHANGED": 0}
    changed_cases = []

    for name, runner in iter_cases():
        payload = capture(name, runner)
        status = _write(name, payload)
        counts[status] += 1
        if status != "unchanged":
            changed_cases.append(f"  {status:9s} {name}")
        print(f"{status:9s} {name}")

    print("\n" + "=" * 62)
    print(
        f"created={counts['created']}  unchanged={counts['unchanged']}  "
        f"changed={counts['CHANGED']}"
    )
    if counts["CHANGED"]:
        print("\nCases whose recorded numbers MOVED:")
        print("\n".join(c for c in changed_cases if "CHANGED" in c))
        print(
            "\nIf you did not intend to change the demonstration numbers,\n"
            "revert these files -- they are NIW/USPTO-facing."
        )
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
