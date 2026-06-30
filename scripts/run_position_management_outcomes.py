#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path

from stockml.diagnostics.position_management_outcomes import build_latest_position_management_outcomes, write_position_management_outcomes


def main() -> int:
    result = write_position_management_outcomes(build_latest_position_management_outcomes())
    print("position_management_outcomes_status:", result.summary.get("status"))
    print("report_path:", Path(result.report_path).resolve())
    print("summary_csv_path:", Path(result.summary_csv_path).resolve())
    print("summary_path:", Path(result.summary_path).resolve())
    for key, value in result.summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
