from __future__ import annotations

from stockml.intraday.promotion_score import score_unscored_snapshots


def main() -> None:
    result = score_unscored_snapshots()
    print("intraday_promotion_status:", result.get("status"))
    print("snapshots_scored:", result.get("snapshots_scored", 0))
    print("verdict_counts:", result.get("verdict_counts", {}))


if __name__ == "__main__":
    main()
