#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path

from stockml.diagnostics.candidate_trade_attribution import build_latest_candidate_trade_attribution, write_candidate_trade_attribution


def main() -> int:
    result = write_candidate_trade_attribution(build_latest_candidate_trade_attribution())
    print("candidate_trade_attribution_status:", result.summary.get("status"))
    print("report_path:", Path(result.report_path).resolve())
    print("summary_path:", Path(result.summary_path).resolve())
    for key, value in result.summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
