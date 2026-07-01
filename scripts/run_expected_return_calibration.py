from __future__ import annotations

import argparse
from pathlib import Path

from stockml.diagnostics.expected_return_calibration import build_latest_expected_return_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description="Build expected trade return calibration diagnostics.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("data/model_outputs/diagnostics"))
    args = parser.parse_args()
    outputs = build_latest_expected_return_calibration(args.root, output_dir=args.output_dir)
    print(f"expected_return_calibration_status: ok")
    print(f"rows: {outputs.rows}")
    print(f"unrealistic_expected_returns: {outputs.unrealistic_rows}")
    print(f"calibrated_rows: {outputs.calibrated_rows}")
    print(f"executable_rows: {outputs.executable_rows}")
    print(f"diagnostic_path: {outputs.diagnostic_path}")
    print(f"summary_path: {outputs.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
