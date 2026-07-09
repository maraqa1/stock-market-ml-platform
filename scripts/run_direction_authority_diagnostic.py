from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates, latest_candidate_or_plan
from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.candidate_pool_export import write_direction_authority_candidate_splits


def _counts(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame.columns:
        return [f"- {column}: missing"]
    counts = frame[column].fillna("NA").astype(str).value_counts().head(25)
    return [f"- {key}: {value}" for key, value in counts.items()]


def main() -> int:
    source_path, candidates = latest_candidate_or_plan()
    stamp = timestamp()
    out_dir = PROJECT_ROOT / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / f"direction_authority_detail_{stamp}.csv"
    summary_path = out_dir / f"direction_authority_summary_{stamp}.md"

    if source_path is None or candidates.empty:
        pd.DataFrame([{"status": "missing_data", "missing_inputs": "latest_candidate_or_plan"}]).to_csv(detail_path, index=False)
        summary_path.write_text("# Direction Authority Diagnostic\n\n- status: missing_data\n- missing_inputs: latest_candidate_or_plan\n", encoding="utf-8")
        print("direction_authority_status: missing_data")
        print(f"detail_path: {detail_path}")
        print(f"summary_path: {summary_path}")
        return 0

    detail = build_execution_ranked_candidates(candidates)
    detail.to_csv(detail_path, index=False)
    split_paths = write_direction_authority_candidate_splits(detail, output_dir=out_dir, stamp=stamp)

    source = detail.get("source_approved_direction", pd.Series("", index=detail.index)).fillna("").astype(str)
    planner = detail.get("planner_derived_direction", pd.Series("", index=detail.index)).fillna("").astype(str)
    primary = detail.get("primary_block_reason", pd.Series("", index=detail.index)).fillna("").astype(str)
    prob = detail.get("probability_calibration_status", pd.Series("", index=detail.index)).fillna("").astype(str)
    executable = detail.get("executable", pd.Series(False, index=detail.index)).fillna(False).astype(bool)
    conflict = detail.get("direction_conflict", pd.Series(False, index=detail.index)).fillna(False).astype(bool)
    status = detail.get("status", pd.Series("", index=detail.index)).fillna("").astype(str).str.lower()

    planner_only = source.eq("NONE") & planner.isin(["LONG", "SHORT"])
    blank_rejected_reasons = status.isin(["blocked", "research_only"]) & primary.str.strip().isin(["", "nan", "None", "NA"])
    lines = [
        "# Direction Authority Diagnostic",
        f"- source_path: `{source_path}`",
        f"- detail_path: `{detail_path}`",
        f"- execution_candidate_pool: `{split_paths['execution_candidate_pool']}`",
        f"- blocked_candidate_pool: `{split_paths['blocked_candidate_pool']}`",
        f"- research_candidate_pool: `{split_paths['research_candidate_pool']}`",
        f"- total_candidates: {len(detail)}",
        f"- source_approved_long_count: {int(source.eq('LONG').sum())}",
        f"- source_approved_short_count: {int(source.eq('SHORT').sum())}",
        f"- no_decision_count: {int(source.eq('NONE').sum())}",
        f"- planner_derived_only_count: {int(planner_only.sum())}",
        f"- memory_aligned_count: {int(detail.get('direction_alignment_status', pd.Series('', index=detail.index)).eq('aligned').sum())}",
        f"- memory_conflict_count: {int(conflict.sum())}",
        f"- memory_insufficient_count: {int(detail.get('direction_alignment_status', pd.Series('', index=detail.index)).eq('memory_insufficient').sum())}",
        f"- executable_count: {int(executable.sum())}",
        f"- blocked_by_direction_count: {int(primary.isin(['planner_derived_action_without_source_approval', 'direction_memory_conflict', 'direction_memory_insufficient', 'source_trade_action_not_executable']).sum())}",
        f"- blocked_by_short_validation_count: {int(primary.eq('short_side_validation_required').sum())}",
        f"- rejected_rows_with_blank_primary_block_reason: {int(blank_rejected_reasons.sum())}",
        f"- rows_with_uncalibrated_probability: {int(prob.eq('uncalibrated').sum())}",
        "\n## executable_direction_status",
        *_counts(detail, "executable_direction_status"),
        "\n## primary_block_reason",
        *_counts(detail, "primary_block_reason"),
        "\n## probability_calibration_status",
        *_counts(detail, "probability_calibration_status"),
        "\n## final_executable_list",
    ]
    executable_rows = detail[executable].sort_values("execution_rank", kind="mergesort")
    if executable_rows.empty:
        lines.append("- none")
    else:
        for row in executable_rows.to_dict("records"):
            lines.append(f"- {row.get('execution_rank')}: {row.get('symbol')} {row.get('side')} raw_rank={row.get('raw_rank')}")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("direction_authority_status: ok")
    print(f"source_path: {source_path}")
    print(f"detail_path: {detail_path}")
    print(f"summary_path: {summary_path}")
    print(f"executable_count: {int(executable.sum())}")
    print(f"direction_conflict_count: {int(conflict.sum())}")
    print(f"planner_only_blocked_count: {int(planner_only.sum())}")
    print(f"probability_uncalibrated_count: {int(prob.eq('uncalibrated').sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
