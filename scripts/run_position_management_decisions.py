from __future__ import annotations

from stockml.trading.position_management_decision import run_position_management_decisions


def main() -> int:
    result = run_position_management_decisions()
    print(f"position_management_decisions_status: {result['status']}")
    print(f"rows: {result['rows']}")
    print(f"csv_path: {result['csv_path']}")
    print(f"markdown_path: {result['markdown_path']}")
    for action, count in result["action_counts"].items():
        print(f"action_count_{action}: {count}")
    print(f"execution_unchanged: {result['execution_unchanged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

