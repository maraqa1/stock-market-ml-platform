from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.calibration_coverage_debug import write_calibration_coverage_debug


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug validation bucket expected-return calibration coverage.")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    outputs = write_calibration_coverage_debug(root=args.root, output_dir=args.output_dir)
    print("calibration_coverage_debug_status: ok")
    print(f"validation_inputs_found: {outputs.validation_inputs_found}")
    print("forward_label_coverage:")
    for column, coverage in outputs.forward_label_coverage.items():
        print(f"  {column}: {coverage:.4f}")
    print(f"bucket_count: {outputs.bucket_count}")
    print(f"usable_bucket_count: {outputs.usable_bucket_count}")
    print(f"candidate_mapping_coverage: {outputs.candidate_mapping_coverage}")
    print(f"root_cause: {outputs.root_cause}")
    print(f"recommended_next_fix: {outputs.recommended_next_fix}")
    print(f"diagnostic_path: {outputs.diagnostic_path}")
    print(f"summary_path: {outputs.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
