from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.validation_bucket_calibration import build_latest_validation_bucket_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description="Build validation bucket expected-return calibration.")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    outputs = build_latest_validation_bucket_calibration(args.root, output_dir=args.output_dir)
    status = "ok" if outputs.usable_buckets > 0 else "insufficient_data"
    print(f"validation_bucket_calibration_status: {status}")
    print(f"calibration_source: {outputs.calibration_source}")
    print(f"gold_path: {outputs.gold_path or ''}")
    print(f"gold_rows_read: {outputs.gold_rows_read}")
    print(f"label_column_used: {outputs.label_column_used}")
    print(f"max_label_date_used: {outputs.max_label_date_used}")
    print(f"excluded_recent_rows: {outputs.excluded_recent_rows}")
    print(f"validation_rows_used: {outputs.validation_rows_used}")
    print(f"buckets_built: {outputs.buckets_built}")
    print(f"usable_buckets: {outputs.usable_buckets}")
    print(f"weak_buckets: {outputs.weak_buckets}")
    print(f"insufficient_buckets: {outputs.insufficient_buckets}")
    print(f"calibration_path: {outputs.calibration_path}")
    print(f"latest_path: {outputs.latest_path}")
    print(f"summary_path: {outputs.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
