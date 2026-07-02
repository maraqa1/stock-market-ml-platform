from __future__ import annotations

import argparse

from stockml.strategy.strategy_funnel import build_strategy_funnel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.parse_args()
    result = build_strategy_funnel()
    print(f"strategy_funnel_path: {result['csv_path']}")
    print(f"strategy_funnel_summary: {result['markdown_path']}")
    print(f"strategy_funnel_rows: {result['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
