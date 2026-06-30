#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path

from stockml.common.paths import timestamp
from stockml.diagnostics.ranking_polarity_diagnostic import build_ranking_polarity_report


def main() -> int:
    output = build_ranking_polarity_report(timestamp())
    print("ranking_polarity_status:", output.status)
    print("report_path:", Path(output.path).resolve())
    print("rows:", output.rows)
    if output.missing_inputs:
        print("missing_inputs:", ",".join(output.missing_inputs))
    if output.warnings:
        print("warnings:", ",".join(output.warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
