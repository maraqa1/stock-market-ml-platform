from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates, latest_candidate_or_plan
from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.candidate_pool_export import write_direction_authority_candidate_splits


def _series(frame: pd.DataFrame, column: str, default: object = "") -> pd.Series:
    return frame.get(column, pd.Series(default, index=frame.index))


def _counts(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame.columns:
        return [f"- {column}: missing"]
    counts = frame[column].fillna("NA").astype(str).value_counts().head(25)
    return [f"- {key}: {value}" for key, value in counts.items()]


def main() -> int:
    source_path, candidates = latest_candidate_or_plan()
    run_stamp = timestamp()
    out_dir = PROJECT_ROOT / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / f"execution_domain_detail_{run_stamp}.csv"
    summary_path = out_dir / f"execution_domain_summary_{run_stamp}.md"

    if source_path is None or candidates.empty:
        pd.DataFrame([{"status": "missing_data", "missing_inputs": "latest_candidate_or_plan"}]).to_csv(detail_path, index=False)
        summary_path.write_text("# Execution Domain Diagnostic\n\n- status: missing_data\n- missing_inputs: latest_candidate_or_plan\n", encoding="utf-8")
        print("execution_domain_status: missing_data")
        print(f"detail_path: {detail_path}")
        print(f"summary_path: {summary_path}")
        return 0

    detail = build_execution_ranked_candidates(candidates)
    detail.to_csv(detail_path, index=False)
    split_paths = write_direction_authority_candidate_splits(detail, output_dir=PROJECT_ROOT / "data" / "trading" / "exports", stamp=run_stamp)

    domain = _series(detail, "execution_domain").fillna("").astype(str)
    source_direction = _series(detail, "source_approved_direction").fillna("").astype(str)
    final_side = _series(detail, "final_execution_side").fillna("").astype(str)
    execution_eligible = _series(detail, "execution_eligible", False).fillna(False).astype(bool)
    notional = pd.to_numeric(_series(detail, "approved_notional", 0), errors="coerce").fillna(0)
    quantity = pd.to_numeric(_series(detail, "suggested_quantity", 0), errors="coerce").fillna(0)

    shadow_wrongly_eligible = domain.eq("shadow_observation") & execution_eligible
    source_wrongly_shadow = source_direction.isin(["LONG", "SHORT"]) & domain.eq("shadow_observation")
    side_conflict = final_side.ne("NONE") & ~domain.eq("execution_candidate")
    execution_missing_size = domain.eq("execution_candidate") & (~notional.gt(0) | ~quantity.gt(0))
    engine_rejected = ~domain.eq("execution_candidate") | ~execution_eligible | ~final_side.isin(["LONG", "SHORT"])

    lines = [
        "# Execution Domain Diagnostic",
        f"- source_path: `{source_path}`",
        f"- detail_path: `{detail_path}`",
        f"- execution_candidate_pool: `{split_paths['execution_candidate_pool']}`",
        f"- watch_candidate_pool: `{split_paths['watch_candidate_pool']}`",
        f"- blocked_candidate_pool: `{split_paths['blocked_candidate_pool']}`",
        f"- shadow_observation_pool: `{split_paths['shadow_observation_pool']}`",
        f"- total_rows: {len(detail)}",
        f"- execution_candidate_count: {int(domain.eq('execution_candidate').sum())}",
        f"- watch_candidate_count: {int(domain.eq('watch_candidate').sum())}",
        f"- blocked_candidate_count: {int(domain.eq('blocked_candidate').sum())}",
        f"- shadow_observation_count: {int(domain.eq('shadow_observation').sum())}",
        f"- source_approved_long_count: {int(source_direction.eq('LONG').sum())}",
        f"- source_approved_short_count: {int(source_direction.eq('SHORT').sum())}",
        f"- no_decision_count: {int(source_direction.eq('NONE').sum())}",
        f"- shadow_observation_incorrectly_execution_eligible: {int(shadow_wrongly_eligible.sum())}",
        f"- source_approved_signal_incorrectly_shadow: {int(source_wrongly_shadow.sum())}",
        f"- final_execution_side_conflicts_with_domain: {int(side_conflict.sum())}",
        f"- execution_candidate_missing_notional_or_quantity: {int(execution_missing_size.sum())}",
        f"- execution_engine_would_reject_non_execution_domain_rows: {int(engine_rejected.sum())}",
        "\n## execution_domain",
        *_counts(detail, "execution_domain"),
        "\n## execution_domain_reason",
        *_counts(detail, "execution_domain_reason"),
        "\n## primary_block_reason",
        *_counts(detail, "primary_block_reason"),
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("execution_domain_status: ok")
    print(f"source_path: {source_path}")
    print(f"detail_path: {detail_path}")
    print(f"summary_path: {summary_path}")
    print(f"execution_candidate_count: {int(domain.eq('execution_candidate').sum())}")
    print(f"watch_candidate_count: {int(domain.eq('watch_candidate').sum())}")
    print(f"blocked_candidate_count: {int(domain.eq('blocked_candidate').sum())}")
    print(f"shadow_observation_count: {int(domain.eq('shadow_observation').sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
