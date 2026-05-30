from __future__ import annotations

from stockml.autopilot.same_day_promotion import evaluate_and_record


def main() -> None:
    result = evaluate_and_record()
    print("same_day_promotion_status:", "met" if result.get("criteria_met") else "not_met")
    print("evaluated_at:", result.get("evaluated_at", ""))
    print("activated:", result.get("activated", False))
    for row in result.get("criteria_results", []):
        print(f"criterion:{row.get('name')} met={row.get('met')} observed={row.get('observed')} threshold={row.get('threshold')}")


if __name__ == "__main__":
    main()
