#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path

from stockml.diagnostics.missed_better_candidates import build_latest_missed_better_candidates, write_missed_better_candidates


def main() -> int:
    result = write_missed_better_candidates(build_latest_missed_better_candidates())
    print("missed_better_candidates_status:", result.summary.get("status"))
    print("report_path:", Path(result.report_path).resolve())
    print("summary_path:", Path(result.summary_path).resolve())
    for key, value in result.summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
