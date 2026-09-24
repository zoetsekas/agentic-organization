"""Fail when a package's line coverage drops below its recorded floor.

Reads the JSON report `pytest --cov=orgagents --cov-report=json` writes and
sums covered and total statements per first-level package of `orgagents`
(a top-level module such as `orgagents/api.py` counts as its own "package").

The floors were measured, not chosen: each is the value CI measured when it
was set, rounded down to a whole percent, less one. Raise a floor when a
package's coverage rises; lowering one is a decision to say so in review.

    python scripts/coverage_floor.py coverage.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

# Measured on Linux, Python 3.12, `.[dev]` with PostgreSQL available, at the
# commit that introduced this file (measured value in the comment). The
# `mcp` extra was not installed for that run, so mcp_server's floor is what
# runs without it; CI installs it and measures higher.
FLOORS: dict[str, float] = {
    "compiler": 94,      # 95.23
    "spec": 92,          # 93.24
    "runtime": 76,       # 77.53
    "designer": 91,      # 92.64
    "metamodel": 94,     # 95.28
    "persistence": 92,   # 93.35
    "api": 85,           # 86.16
    "cli": 49,           # 50.05
    "fabric": 95,        # 96.70
    "catalogs": 94,      # 95.92
    "harness": 74,       # 75.36
    "tasks": 96,         # 97.39
    "channels": 95,      # 96.18
    "security": 91,      # 92.70
    "mcp_server": 2,     # 3.22 without the mcp extra
}
# Everything not listed above, taken together.
OTHER_FLOOR = 91     # 92.09
TOTAL_FLOOR = 87     # 88.11


def package_of(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    i = parts.index("orgagents")
    rest = parts[i + 1:]
    return rest[0][:-3] if len(rest) == 1 and rest[0].endswith(".py") else rest[0]


def main(report: str) -> int:
    files = json.load(open(report, encoding="utf-8"))["files"]
    covered: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for path, data in files.items():
        if "orgagents" not in path.replace("\\", "/").split("/"):
            continue
        pkg = package_of(path)
        pkg = pkg if pkg in FLOORS else "(other)"
        covered[pkg] += data["summary"]["covered_lines"]
        total[pkg] += data["summary"]["num_statements"]
    failed = False
    all_cov, all_tot = sum(covered.values()), sum(total.values())
    rows = sorted(total) + ["(total)"]
    for pkg in rows:
        c, t = (all_cov, all_tot) if pkg == "(total)" else (covered[pkg], total[pkg])
        pct = 100.0 * c / t if t else 100.0
        floor = {"(other)": OTHER_FLOOR, "(total)": TOTAL_FLOOR}.get(pkg, FLOORS.get(pkg, 0.0))
        ok = pct >= floor
        failed |= not ok
        print(f"{'ok ' if ok else 'LOW'} {pkg:<22} {pct:6.2f}%  floor {floor:5.1f}%  ({c}/{t})")
    if failed:
        print("coverage fell below a floor (scripts/coverage_floor.py)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
