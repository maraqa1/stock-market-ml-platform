from __future__ import annotations

import argparse
from pathlib import Path

from stockml.trading.counterfactual_log import write_counterfactual_forward_returns


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counterfactual-file", default=None)
    parser.add_argument("--gold-file", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    result = write_counterfactual_forward_returns(
        args.counterfactual_file,
        gold_path=Path(args.gold_file) if args.gold_file else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print("counterfactual_forward_returns_status: ok")
    print("counterfactual_forward_returns_path:", result.path)
    print("rows:", result.rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
