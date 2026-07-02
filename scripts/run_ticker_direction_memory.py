from __future__ import annotations

from stockml.diagnostics.ticker_direction_memory import run_ticker_direction_memory


def main() -> int:
    result = run_ticker_direction_memory()
    print(f"ticker_direction_memory_status: {result['status']}")
    print(f"rows: {result['rows']}")
    print(f"trust_original: {result['trust_original']}")
    print(f"inverse_watch: {result['inverse_watch']}")
    print(f"insufficient_data: {result['insufficient_data']}")
    print(f"source_path: {result['source_path']}")
    print(f"csv_path: {result['csv_path']}")
    print(f"markdown_path: {result['markdown_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
