from __future__ import annotations

import argparse

from stockml.intraday.promotion_score import explain_latest_snapshot, score_unscored_snapshots


def _print_explanation(payload: dict) -> None:
    print("intraday_promotion_explain_status:", payload.get("status"))
    for key in [
        "symbol",
        "snapshot_id",
        "snapshot_at",
        "nightly_bias",
        "nightly_score",
        "spread_bps",
        "dollar_volume_today",
        "trend_5m_pct",
        "trend_15m_pct",
        "intraday_range_position",
    ]:
        print(f"{key}:", payload.get(key))
    decision = payload.get("decision")
    if decision is not None:
        print("decision_verdict:", decision.verdict)
        print("decision_block_reason:", decision.block_reason)
        print("decision_promotion_score:", decision.promotion_score)
        print("decision_contributing:", decision.contributing)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score intraday candidate promotion snapshots.")
    parser.add_argument("--explain-symbol", help="Evaluate the latest snapshot for one symbol with current code/config without writing a log row.")
    args = parser.parse_args()
    if args.explain_symbol:
        _print_explanation(explain_latest_snapshot(args.explain_symbol))
        return

    result = score_unscored_snapshots()
    print("intraday_promotion_status:", result.get("status"))
    print("snapshots_scored:", result.get("snapshots_scored", 0))
    print("verdict_counts:", result.get("verdict_counts", {}))


if __name__ == "__main__":
    main()
