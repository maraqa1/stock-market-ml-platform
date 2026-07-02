from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.execution_ranker import latest_candidate_or_plan
from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.direction_gate import evaluate_direction_gate


DIAGNOSTIC_COLUMNS = [
    "symbol",
    "side",
    "raw_rank",
    "execution_rank",
    "source_trade_action",
    "trade_action",
    "directional_action",
    "candidate_source",
    "validated_expected_return_bps",
    "validated_profit_factor",
    "meta_label_decision",
    "short_policy_status",
    "inverse_watch_flag",
    "direction_gate_status",
    "direction_gate_pass",
    "direction_decision",
    "direction_confidence",
    "direction_primary_reason",
    "direction_blocking_reasons",
    "direction_supporting_reasons",
    "ticker_direction_bias",
    "ticker_direction_confidence",
    "ticker_direction_sample_count",
    "ticker_inverse_advantage_bps",
    "ticker_direction_reason",
    "executable_before_direction_gate",
    "executable_after_direction_gate",
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    return text in {"true", "1", "yes", "approved", "executable"}


def _symbol(row: Any) -> str:
    return (_text(row.get("symbol")) or _text(row.get("ticker"))).upper()


def _raw_rank(frame: pd.DataFrame) -> pd.Series:
    for column in ["raw_rank", "rank_overall", "candidate_rank", "research_rank"]:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(range(1, len(frame) + 1), index=frame.index, dtype="float64")


def build_direction_gate_diagnostic(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=DIAGNOSTIC_COLUMNS)
    frame = candidates.copy()
    if "raw_rank" not in frame.columns:
        frame["raw_rank"] = _raw_rank(frame)
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        gate = evaluate_direction_gate(row)
        before = _boolish(row.get("executable")) or _text(row.get("trade_quality_status")).lower() == "approved"
        after = before and bool(gate["direction_gate_pass"]) and gate["direction_decision"] == "direction_pass"
        rows.append(
            {
                "symbol": _symbol(row),
                "side": _text(row.get("side")).lower(),
                "raw_rank": row.get("raw_rank", ""),
                "execution_rank": row.get("execution_rank", ""),
                "source_trade_action": row.get("source_trade_action", ""),
                "trade_action": row.get("trade_action", ""),
                "directional_action": row.get("directional_action", ""),
                "candidate_source": row.get("candidate_source", ""),
                "validated_expected_return_bps": row.get("validated_expected_return_bps", ""),
                "validated_profit_factor": row.get("validated_profit_factor", ""),
                "meta_label_decision": row.get("meta_label_decision", ""),
                "short_policy_status": row.get("short_policy_status", ""),
                "inverse_watch_flag": row.get("inverse_watch_flag", ""),
                "ticker_direction_bias": row.get("ticker_direction_bias", ""),
                "ticker_direction_confidence": row.get("ticker_direction_confidence", ""),
                "ticker_direction_sample_count": row.get("ticker_direction_sample_count", ""),
                "ticker_inverse_advantage_bps": row.get("ticker_inverse_advantage_bps", ""),
                "ticker_direction_reason": row.get("ticker_direction_reason", ""),
                "executable_before_direction_gate": before,
                "executable_after_direction_gate": after,
                **gate,
            }
        )
    return pd.DataFrame(rows).reindex(columns=DIAGNOSTIC_COLUMNS)


def _write_markdown(frame: pd.DataFrame, path: Path, *, source_path: Path | None) -> None:
    counts = frame["direction_decision"].value_counts().to_dict() if not frame.empty else {}
    no_decision = frame[
        frame["source_trade_action"].fillna("").astype(str).str.lower().isin(["no decision", "no_decision", ""])
    ]
    planner_derived = frame[frame["direction_primary_reason"].eq("planner_derived_action_without_source_approval")]
    shorts = frame[
        frame["direction_primary_reason"].fillna("").astype(str).str.contains("short_side_validation_required", regex=False)
    ]
    negative = frame[frame["direction_primary_reason"].eq("negative_validated_expected_return")]
    profit_factor = frame[frame["direction_primary_reason"].eq("validated_profit_factor_below_one")]
    pass_rows = frame[frame["direction_decision"].eq("direction_pass")].head(10)
    blocked_rows = frame[~frame["direction_decision"].eq("direction_pass")].head(15)

    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Direction Gate Diagnostic\n\n")
        handle.write(f"- Generated at UTC: `{datetime.now(timezone.utc).isoformat()}`\n")
        handle.write(f"- Source: `{source_path or ''}`\n")
        handle.write(f"- Total candidates analysed: `{len(frame)}`\n")
        handle.write(f"- direction_pass: `{counts.get('direction_pass', 0)}`\n")
        handle.write(f"- direction_block: `{counts.get('direction_block', 0)}`\n")
        handle.write(f"- direction_research_only: `{counts.get('direction_research_only', 0)}`\n")
        handle.write(f"- direction_inverse_watch: `{counts.get('direction_inverse_watch', 0)}`\n")
        handle.write(f"- direction_manual_review: `{counts.get('direction_manual_review', 0)}`\n")
        handle.write(f"- No Decision blocked/research-only count: `{len(no_decision)}`\n")
        handle.write(f"- Planner-derived action blocked count: `{len(planner_derived)}`\n")
        handle.write(f"- Short blocked/research-only count: `{len(shorts)}`\n")
        handle.write(f"- Negative expected return blocked count: `{len(negative)}`\n")
        handle.write(f"- Profit factor blocked count: `{len(profit_factor)}`\n\n")
        handle.write("## Direction Risk Conclusion\n\n")
        handle.write(
            "Only candidates with authoritative `source_trade_action` and positive calibrated direction evidence "
            "should be executable. Planner-derived Long/Short rows from `No Decision` remain research-only.\n\n"
        )
        handle.write("## Recommended Execution Policy\n\n")
        handle.write("- Execute only rows where `direction_decision = direction_pass`.\n")
        handle.write("- Keep shorts research-only unless short-side validation explicitly passes.\n")
        handle.write("- Do not use raw `expected_trade_return` as direction evidence.\n\n")
        for title, sample in [("Top Pass Candidates", pass_rows), ("Top Blocked Candidates", blocked_rows)]:
            handle.write(f"## {title}\n\n")
            if sample.empty:
                handle.write("None.\n\n")
                continue
            columns = [
                "symbol",
                "side",
                "raw_rank",
                "source_trade_action",
                "trade_action",
                "validated_expected_return_bps",
                "validated_profit_factor",
                "direction_decision",
                "direction_primary_reason",
            ]
            sample = sample[[column for column in columns if column in sample.columns]].copy()
            headers = list(sample.columns)
            handle.write("| " + " | ".join(headers) + " |\n")
            handle.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
            for row in sample.fillna("").astype(str).to_dict("records"):
                handle.write("| " + " | ".join(str(row.get(header, "")).replace("|", "/") for header in headers) + " |\n")
            handle.write("\n")


def run_direction_gate_diagnostic(
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> dict[str, Any]:
    base = Path(root) if root else PROJECT_ROOT
    out_dir = Path(output_dir) if output_dir else base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path, candidates = latest_candidate_or_plan(base)
    run_stamp = stamp or timestamp()
    csv_path = out_dir / f"direction_gate_diagnostic_{run_stamp}.csv"
    md_path = out_dir / f"direction_gate_diagnostic_{run_stamp}.md"
    frame = build_direction_gate_diagnostic(candidates)
    frame.to_csv(csv_path, index=False)
    _write_markdown(frame, md_path, source_path=source_path)
    counts = frame["direction_decision"].value_counts().to_dict() if not frame.empty else {}
    return {
        "status": "ok" if source_path is not None else "missing_data",
        "source_path": str(source_path or ""),
        "csv_path": str(csv_path),
        "markdown_path": str(md_path),
        "rows": len(frame),
        "direction_pass": int(counts.get("direction_pass", 0)),
        "direction_block": int(counts.get("direction_block", 0)),
        "direction_research_only": int(counts.get("direction_research_only", 0)),
        "direction_inverse_watch": int(counts.get("direction_inverse_watch", 0)),
        "direction_manual_review": int(counts.get("direction_manual_review", 0)),
        "no_decision_blocked": int(
            frame["source_trade_action"].fillna("").astype(str).str.lower().isin(["no decision", "no_decision", ""]).sum()
        )
        if not frame.empty
        else 0,
        "short_blocked": int(frame["direction_primary_reason"].fillna("").astype(str).str.contains("short_side_validation_required", regex=False).sum())
        if not frame.empty
        else 0,
    }
