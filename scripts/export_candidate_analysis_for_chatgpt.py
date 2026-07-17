from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def latest_file(directory: Path, pattern: str) -> Path:
    files = [path for path in directory.glob(pattern) if path.is_file()]
    if not files:
        raise SystemExit(f"missing file pattern: {directory / pattern}")
    return max(files, key=lambda path: path.stat().st_mtime)


def analysis_note(row: pd.Series) -> str:
    domain = str(row.get("execution_domain", "") or "").strip()
    reason = str(row.get("primary_block_reason", "") or "").strip()
    if domain == "execution_candidate":
        return "EXECUTABLE: candidate can be considered by paper autopilot"
    if domain == "shadow_observation":
        return "SHADOW: no source-approved direction; planner-only row should not trade"
    if reason:
        return f"BLOCKED/WATCH: {reason}"
    return f"REVIEW: {domain or 'unknown'}"


def markdown_counts(series: pd.Series) -> str:
    rows = [("| value | count |"), ("|---|---:|")]
    for value, count in series.items():
        rows.append(f"| {str(value).replace('|', '/')} | {int(count)} |")
    return "\n".join(rows)


def markdown_frame(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    columns = list(frame.columns)
    rows = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        values = []
        for column in columns:
            value = row.get(column, "")
            if pd.isna(value):
                value = ""
            values.append(str(value).replace("|", "/").replace("\n", " "))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def main() -> int:
    portal_dir = Path("data/portal_outputs")
    source = latest_file(portal_dir, "execution_ranked_candidates_*.csv")
    frame = pd.read_csv(source, low_memory=False)

    wanted = [
        "execution_rank",
        "raw_rank",
        "model_rank",
        "research_rank",
        "symbol",
        "side",
        "status",
        "execution_domain",
        "execution_eligible",
        "execution_pool_eligible",
        "trade_authority_status",
        "primary_block_reason",
        "all_block_reasons",
        "source_trade_action",
        "trade_action",
        "directional_action",
        "final_proposed_side",
        "final_execution_side",
        "source_approved_direction",
        "planner_derived_direction",
        "direction_resolution",
        "direction_resolution_reason",
        "direction_gate_status",
        "direction_gate_pass",
        "direction_decision",
        "direction_primary_reason",
        "ticker_direction_bias",
        "ticker_direction_memory_status",
        "ticker_direction_sample_count",
        "ticker_direction_confidence",
        "validated_expected_return_bps",
        "validated_hit_rate",
        "validated_profit_factor",
        "expected_return_scope",
        "hit_rate_scope",
        "profit_factor_scope",
        "probability_calibration_status",
        "calibrated_probability_win",
        "probability_usable_for_sizing",
        "confidence_bucket",
        "risk_tier",
        "volatility_tier",
        "liquidity_tier",
        "market_cap",
        "current_price",
        "avg_dollar_volume_20d",
        "volatility_20d",
        "volatility_opportunity_status",
        "volatility_opportunity_reason",
        "volatility_opportunity_allows_reduced_trade",
        "session_mode",
        "session_reject_reason",
        "overnight_tradable",
        "spread_bps",
        "quote_freshness_seconds",
        "approved_notional",
        "suggested_quantity",
        "order_eligible",
        "trade_quality_status",
        "trade_quality_reason",
    ]
    columns = [column for column in wanted if column in frame.columns]
    out = frame[columns].copy()
    out.insert(0, "analysis_note", out.apply(analysis_note, axis=1))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = Path("data/trading/exports")
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"chatgpt_last_100_candidates_analysis_{stamp}.csv"
    md_path = output_dir / f"chatgpt_last_100_candidates_analysis_{stamp}.md"
    out.to_csv(csv_path, index=False)

    preview_columns = [
        column
        for column in [
            "execution_rank",
            "raw_rank",
            "symbol",
            "side",
            "status",
            "execution_domain",
            "primary_block_reason",
            "validated_expected_return_bps",
            "validated_hit_rate",
            "validated_profit_factor",
            "risk_tier",
            "volatility_tier",
            "volatility_opportunity_status",
            "approved_notional",
            "suggested_quantity",
            "analysis_note",
        ]
        if column in out.columns
    ]
    lines = [
        "# Last 100 Candidates Analysis",
        "",
        f"Source: `{source}`",
        f"Rows: {len(out)}",
    ]
    for column in ["execution_domain", "status", "primary_block_reason", "volatility_opportunity_status", "side"]:
        if column in out.columns:
            counts = out[column].fillna("NA").astype(str).value_counts().head(20)
            lines.extend(["", f"## {column}", "", markdown_counts(counts)])
    lines.extend(["", "## Top Rows", "", markdown_frame(out[preview_columns].head(30))])
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"source_path: {source}")
    print(f"csv_path: {csv_path}")
    print(f"markdown_path: {md_path}")
    print(f"rows: {len(out)}")
    if "execution_domain" in out.columns:
        print("execution_domain_counts:", out["execution_domain"].fillna("NA").astype(str).value_counts().to_dict())
    if "primary_block_reason" in out.columns:
        print("top_block_reasons:", out["primary_block_reason"].fillna("NA").astype(str).value_counts().head(10).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
