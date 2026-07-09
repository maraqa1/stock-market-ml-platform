from __future__ import annotations

import argparse
from pathlib import Path

from stockml.common.paths import timestamp
from stockml.diagnostics.gold_model_coverage_audit import write_gold_model_coverage_audit
from stockml.diagnostics.top_mover_lineage import normalize_movers, write_top_mover_lineage


def _symbols(value: str) -> list[str]:
    return [part.strip().upper() for part in value.replace(";", ",").split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace external top movers through StockML pipeline lineage.")
    parser.add_argument("--date", default="", help="Observation date for the external movers.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols to trace.")
    parser.add_argument("--input-csv", default="", help="Optional CSV with symbol and screenshot metadata.")
    args = parser.parse_args()

    movers = normalize_movers(_symbols(args.symbols), Path(args.input_csv) if args.input_csv else None)
    if args.date and "observed_at" in movers.columns:
        movers.loc[movers["observed_at"].fillna("").astype(str).str.strip().eq(""), "observed_at"] = args.date
    run_stamp = timestamp()
    lineage = write_top_mover_lineage(movers, stamp=run_stamp)
    audit = write_gold_model_coverage_audit(stamp=run_stamp, mover_symbols=set(movers["normalized_symbol"].astype(str).str.upper()))
    detail = lineage.detail
    funnel = {
        "universe": int(detail["universe_present"].fillna(False).astype(bool).sum()),
        "price_history": int(detail["price_history_present"].fillna(False).astype(bool).sum()),
        "validated_universe": int(detail["validated_universe_present"].fillna(False).astype(bool).sum()),
        "gold": int(detail["gold_v2_present"].fillna(False).astype(bool).sum()),
        "model_signal": int(detail["model_signal_present"].fillna(False).astype(bool).sum()),
        "candidate_pool": int(detail["candidate_pool_present"].fillna(False).astype(bool).sum()),
        "execution": int(detail["execution_domain"].fillna("").astype(str).eq("execution_candidate").sum()),
        "order_plan": int(detail["order_plan_present"].fillna(False).astype(bool).sum()),
    }
    root_counts = detail["root_cause_stage"].fillna("unknown").astype(str).value_counts().to_dict()
    axon = detail[detail["normalized_symbol"].eq("AXON")]
    flex = detail[detail["normalized_symbol"].eq("FLEX")]
    print("top_mover_lineage_status: ok")
    print(f"detail_path: {lineage.detail_path}")
    print(f"summary_path: {lineage.summary_path}")
    print(f"coverage_path: {audit.csv_path}")
    print(f"coverage_summary_path: {audit.summary_path}")
    print(f"mover_funnel_counts: {funnel}")
    print(f"top_root_cause_stages: {root_counts}")
    if not axon.empty:
        row = axon.iloc[0]
        print(f"axon_diagnosis: root={row['root_cause_stage']} reason={row['root_cause_reason']} strong_long_miss={row['strong_long_missed_by_source_action']}")
    if not flex.empty:
        row = flex.iloc[0]
        print(f"flex_diagnosis: root={row['root_cause_stage']} reason={row['root_cause_reason']} aligned_no_decision={row['long_mover_memory_aligned_but_no_decision']}")
    print(f"same_day_mover_watch_lane_recommended: {int(funnel['candidate_pool']) == 0 and len(detail) > 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
