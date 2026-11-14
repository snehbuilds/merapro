"""Per-area coverage gates (M11.2; docs/10 §7: "coverage gates are per area, not global").

Reads ``coverage.json`` (``pytest --cov=mera --cov-report=json``) and fails if any area is
below its floor. The floors for the load-bearing modules are the docs/11 §1 DoD 15 list;
the rest are ratchets at today's level, raised as tests are added, never lowered.

    uv run python scripts/coverage_gates.py coverage.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# (path prefix under src/mera, minimum line coverage %)
GATES: list[tuple[str, float]] = [
    ("domain/state_machines/", 100.0),
    ("domain/policies/rbac.py", 100.0),
    ("domain/policies/settings_resolution.py", 100.0),
    ("domain/policies/content_hash.py", 100.0),
    ("domain/policies/scheduling.py", 95.0),
    ("domain/policies/", 95.0),
    ("infrastructure/crypto/", 95.0),
    ("application/approvals/publishable.py", 95.0),
    ("application/approvals/", 85.0),
    ("application/publishing/", 85.0),
    ("application/channels/", 75.0),
    ("infrastructure/platforms/adapters/", 75.0),
    ("application/services/", 75.0),
    ("", 85.0),  # the whole package
]


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = {k.replace("\\", "/"): v["summary"] for k, v in data["files"].items()}
    failures = []
    for prefix, floor in GATES:
        covered = total = 0
        for name, summary in files.items():
            rel = name.split("src/mera/", 1)[-1] if "src/mera/" in name else name
            if rel.startswith(prefix):
                covered += summary["covered_lines"]
                total += summary["num_statements"]
        if total == 0:
            continue
        pct = 100.0 * covered / total
        status = "ok" if pct + 1e-9 >= floor else "FAIL"
        print(f"{status:4} {prefix or '(all)':45} {pct:6.2f}% (floor {floor:.0f}%)")
        if status == "FAIL":
            failures.append(prefix)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
