from __future__ import annotations

import argparse
from pathlib import Path

from stockml.trading.forward_paper_manifest import write_forward_paper_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-manifest", default=None)
    parser.add_argument("--pipeline-run-id", default=None)
    parser.add_argument("--run-date", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    result = write_forward_paper_manifest(
        pipeline_manifest_path=args.pipeline_manifest,
        pipeline_run_id=args.pipeline_run_id,
        run_date=args.run_date,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print("forward_paper_manifest_status: ok")
    print("forward_paper_manifest_path:", result["path"])
    print("paper_program_status:", result["paper_program_status"])
    print("material_change_flag:", result["material_change_flag"])
    print("live_trading_enabled:", result["live_trading_enabled"])
    print("allow_short_selling:", result["allow_short_selling"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
