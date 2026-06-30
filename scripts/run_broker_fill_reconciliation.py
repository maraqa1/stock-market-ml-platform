#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path

from stockml.diagnostics.broker_fill_reconciliation import build_latest_reconciliation, write_reconciliation


def main() -> int:
    result = write_reconciliation(build_latest_reconciliation())
    print("broker_fill_reconciliation_status:", result.summary.get("status"))
    print("report_path:", Path(result.report_path).resolve())
    print("summary_path:", Path(result.summary_path).resolve())
    for key, value in result.summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
