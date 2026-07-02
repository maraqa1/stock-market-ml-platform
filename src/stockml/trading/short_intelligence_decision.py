from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy
from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.diagnostics.short_side_performance_guard import evaluate_short_side_performance
from stockml.diagnostics.short_squeeze_risk import short_squeeze_risk_for_row


DIAGNOSTIC_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"
DECISION_COLUMNS = [
    "decision_id",
    "generated_at",
    "symbol",
    "candidate_source",
    "raw_rank",
    "execution_rank",
    "side",
    "source_trade_action",
    "directional_action",
    "model_score",
    "rank_overall",
    "predicted_rank_pct",
    "meta_label_probability",
    "meta_label_decision",
    "price",
    "market_cap",
    "risk_tier",
    "volatility_tier",
    "liquidity_tier",
    "spread_bps",
    "gap_pct",
    "intraday_move_pct",
    "relative_volume",
    "shortable",
    "borrow_status",
    "overnight_tradable",
    "session_mode",
    "historical_short_win_rate",
    "historical_short_profit_factor",
    "historical_short_net_return_bps",
    "inverse_long_return_30m_bps",
    "inverse_long_return_1h_bps",
    "inverse_long_return_eod_bps",
    "inverse_long_return_5d_bps",
    "squeeze_risk_score",
    "squeeze_risk_tier",
    "squeeze_risk_reasons",
    "data_quality_status",
    "short_decision",
    "short_decision_strength",
    "primary_reason",
    "supporting_reasons",
    "blocking_reasons",
    "inverse_watch_flag",
    "paper_short_allowed",
    "would_submit_order",
    "diagnostics_only",
]


@dataclass(frozen=True)
class ShortIntelligenceOutputs:
    csv_path: Path
    markdown_path: Path
    decisions: pd.DataFrame
    summary: dict[str, Any]


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _boolish(value: Any) -> bool | None:
    text = _text(value).lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _is_short(row: Any) -> bool:
    side = _text(row.get("side") if hasattr(row, "get") else "").lower()
    action = _text(row.get("trade_action") if hasattr(row, "get") else "").lower()
    source = _text(row.get("source_trade_action") if hasattr(row, "get") else "").lower()
    directional = _text(row.get("directional_action") if hasattr(row, "get") else "").lower()
    return side in {"sell", "short"} or action == "short" or source == "short" or directional == "short"


def _historical_metrics(closed_trades: pd.DataFrame, policy: ShortSidePolicy) -> dict[str, Any]:
    guard = evaluate_short_side_performance(closed_trades, policy=policy)
    if guard.empty:
        return {
            "closed_short_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_return_bps": 0.0,
            "pnl": 0.0,
            "worst_short_symbols": "",
        }
    row = guard.iloc[0]
    return {
        "closed_short_trades": int(row.get("closed_short_trades", 0) or 0),
        "win_rate": float(row.get("short_win_rate", 0) or 0),
        "profit_factor": float(row.get("short_profit_factor", 0) or 0),
        "net_return_bps": float(row.get("short_average_return_bps", 0) or 0),
        "pnl": float(row.get("short_realised_pnl", 0) or 0),
        "worst_short_symbols": row.get("worst_short_symbols", ""),
    }


def _candidate_forward_bps(row: Any) -> float | None:
    for column in ["inverse_long_return_5d_bps", "forward_5d_return_bps", "realised_forward_return_bps"]:
        value = _num(row.get(column) if hasattr(row, "get") else None)
        if value is not None:
            return value
    for column in ["forward_5d_return", "target_return_5d"]:
        value = _num(row.get(column) if hasattr(row, "get") else None)
        if value is not None:
            return value * 10_000 if abs(value) <= 2 else value
    return None


def decide_short_candidate(
    row: Any,
    *,
    historical: dict[str, Any],
    policy: ShortSidePolicy | None = None,
    generated_at: str = "",
) -> dict[str, Any]:
    active_policy = policy or load_short_side_policy()
    symbol = (_text(row.get("symbol") if hasattr(row, "get") else "") or _text(row.get("ticker") if hasattr(row, "get") else "")).upper()
    source_action = _text(row.get("source_trade_action") if hasattr(row, "get") else "") or _text(row.get("trade_action") if hasattr(row, "get") else "")
    directional_action = _text(row.get("directional_action") if hasattr(row, "get") else "")
    squeeze = short_squeeze_risk_for_row(row)
    blocking: list[str] = []
    supporting: list[str] = []
    data_quality = "ok"

    if not active_policy.enabled:
        blocking.append("short_side_execution_disabled")
    if source_action.lower() in {"no decision", "no_decision", "none", ""}:
        blocking.append("source_trade_action_not_executable")
    if historical["closed_short_trades"] < active_policy.min_closed_short_trades_for_enablement:
        blocking.append("insufficient_short_trade_sample")
    if historical["win_rate"] < active_policy.min_short_win_rate_for_enablement:
        blocking.append("short_win_rate_below_threshold")
    if historical["profit_factor"] < active_policy.min_short_profit_factor_for_enablement:
        blocking.append("short_profit_factor_below_threshold")
    if historical["pnl"] < 0 or historical["net_return_bps"] < 0:
        blocking.append("short_negative_edge")

    forward_bps = _candidate_forward_bps(row)
    if forward_bps is None:
        blocking.append("missing_forward_marks")
        data_quality = "insufficient_data"
    inverse_outperforms = forward_bps is not None and forward_bps > 0
    if inverse_outperforms:
        supporting.append("inverse_long_outperforms_short")

    if squeeze["short_squeeze_risk_tier"] == "high":
        blocking.append("squeeze_risk_high")
    if squeeze["short_squeeze_risk_reasons"]:
        supporting.extend(squeeze["short_squeeze_risk_reasons"].split("|"))
    if not squeeze["short_squeeze_risk_reasons"]:
        data_quality = "partial" if data_quality == "ok" else data_quality

    shortable = _boolish(row.get("shortable") if hasattr(row, "get") else None)
    if shortable is False:
        blocking.append("not_shortable")
    borrow = _text(row.get("borrow_status") if hasattr(row, "get") else "").lower()
    if borrow in {"unavailable", "hard_to_borrow", "not_available"}:
        blocking.append("borrow_unavailable")
    overnight = _boolish(row.get("overnight_tradable") if hasattr(row, "get") else None)
    session = _text(row.get("session_mode") if hasattr(row, "get") else "")
    if session == "overnight_24_5" and overnight is False:
        blocking.append("overnight_not_tradable")

    decision = "research_only"
    primary = "short_side_execution_disabled" if not active_policy.enabled else (blocking[0] if blocking else "diagnostics_only")
    inverse_watch = False
    if "squeeze_risk_high" in blocking:
        decision = "inverse_watch"
        primary = "squeeze_risk_high"
        inverse_watch = True
    elif inverse_outperforms or "short_negative_edge" in blocking:
        decision = "inverse_watch"
        primary = "inverse_long_outperforms_short" if inverse_outperforms else "short_negative_edge"
        inverse_watch = True
    elif source_action.lower() in {"no decision", "no_decision", "none", ""}:
        decision = "research_only"
        primary = "source_trade_action_not_executable"
    elif active_policy.enabled and active_policy.allow_shorts_in_validation and not blocking:
        decision = "paper_short_eligible"
        primary = "short_evidence_passed"

    if decision == "paper_short_eligible":
        strength = "high"
    elif decision == "inverse_watch":
        strength = "medium"
    else:
        strength = "low"

    return {
        "decision_id": f"short-intel-{generated_at.replace(':', '').replace('-', '').replace('+', '').replace('T', '-')}-{symbol}",
        "generated_at": generated_at,
        "symbol": symbol,
        "candidate_source": _text(row.get("candidate_source") if hasattr(row, "get") else "") or "candidate_artifact",
        "raw_rank": row.get("raw_rank", "") if hasattr(row, "get") else "",
        "execution_rank": row.get("execution_rank", "") if hasattr(row, "get") else "",
        "side": "Short",
        "source_trade_action": source_action,
        "directional_action": directional_action,
        "model_score": row.get("model_score", "") if hasattr(row, "get") else "",
        "rank_overall": row.get("rank_overall", row.get("raw_rank", "")) if hasattr(row, "get") else "",
        "predicted_rank_pct": row.get("predicted_rank_pct_by_date", row.get("predicted_rank_pct", "")) if hasattr(row, "get") else "",
        "meta_label_probability": row.get("meta_label_probability", "") if hasattr(row, "get") else "",
        "meta_label_decision": row.get("meta_label_decision", "") if hasattr(row, "get") else "",
        "price": row.get("close", row.get("price", "")) if hasattr(row, "get") else "",
        "market_cap": row.get("market_cap", "") if hasattr(row, "get") else "",
        "risk_tier": row.get("risk_tier", "") if hasattr(row, "get") else "",
        "volatility_tier": row.get("volatility_tier", "") if hasattr(row, "get") else "",
        "liquidity_tier": row.get("liquidity_tier", "") if hasattr(row, "get") else "",
        "spread_bps": row.get("spread_bps", "") if hasattr(row, "get") else "",
        "gap_pct": row.get("gap_pct", "") if hasattr(row, "get") else "",
        "intraday_move_pct": row.get("intraday_move_pct", row.get("intraday_return", "")) if hasattr(row, "get") else "",
        "relative_volume": row.get("relative_volume", row.get("volume_ratio_20d", "")) if hasattr(row, "get") else "",
        "shortable": row.get("shortable", "") if hasattr(row, "get") else "",
        "borrow_status": row.get("borrow_status", "") if hasattr(row, "get") else "",
        "overnight_tradable": row.get("overnight_tradable", "") if hasattr(row, "get") else "",
        "session_mode": session,
        "historical_short_win_rate": historical["win_rate"],
        "historical_short_profit_factor": historical["profit_factor"],
        "historical_short_net_return_bps": historical["net_return_bps"],
        "inverse_long_return_30m_bps": row.get("inverse_long_return_30m_bps", "") if hasattr(row, "get") else "",
        "inverse_long_return_1h_bps": row.get("inverse_long_return_1h_bps", "") if hasattr(row, "get") else "",
        "inverse_long_return_eod_bps": row.get("inverse_long_return_eod_bps", "") if hasattr(row, "get") else "",
        "inverse_long_return_5d_bps": forward_bps if forward_bps is not None else "",
        "squeeze_risk_score": squeeze["short_squeeze_risk_score"],
        "squeeze_risk_tier": squeeze["short_squeeze_risk_tier"],
        "squeeze_risk_reasons": squeeze["short_squeeze_risk_reasons"],
        "data_quality_status": data_quality,
        "short_decision": decision,
        "short_decision_strength": strength,
        "primary_reason": primary,
        "supporting_reasons": "|".join(dict.fromkeys(supporting)),
        "blocking_reasons": "|".join(dict.fromkeys(blocking)),
        "inverse_watch_flag": inverse_watch,
        "paper_short_allowed": False,
        "would_submit_order": False,
        "diagnostics_only": True,
    }


def build_short_intelligence_decisions(
    candidates: pd.DataFrame,
    closed_trades: pd.DataFrame | None = None,
    *,
    policy: ShortSidePolicy | None = None,
    generated_at: str | None = None,
) -> pd.DataFrame:
    active_policy = policy or load_short_side_policy()
    run_time = generated_at or pd.Timestamp.utcnow().isoformat()
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=DECISION_COLUMNS)
    historical = _historical_metrics(closed_trades if closed_trades is not None else pd.DataFrame(), active_policy)
    rows = [decide_short_candidate(row, historical=historical, policy=active_policy, generated_at=run_time) for _, row in candidates.iterrows() if _is_short(row)]
    return pd.DataFrame(rows).reindex(columns=DECISION_COLUMNS)


def summarize_short_intelligence(decisions: pd.DataFrame, closed_trades: pd.DataFrame | None = None) -> dict[str, Any]:
    counts = decisions.get("short_decision", pd.Series(dtype=str)).value_counts().to_dict() if decisions is not None and not decisions.empty else {}
    guard = evaluate_short_side_performance(closed_trades if closed_trades is not None else pd.DataFrame())
    historical = guard.iloc[0].to_dict() if not guard.empty else {}
    inverse_symbols = decisions[decisions["short_decision"].eq("inverse_watch")]["symbol"].head(10).tolist() if decisions is not None and not decisions.empty else []
    high_squeeze = decisions[decisions["squeeze_risk_tier"].eq("high")]["symbol"].head(10).tolist() if decisions is not None and not decisions.empty else []
    recommendation = "SHORTS_RESEARCH_ONLY"
    if counts.get("inverse_watch", 0):
        recommendation = "SHORTS_INVERSE_WATCH_ONLY"
    if float(historical.get("short_realised_pnl", 0) or 0) < 0 or float(historical.get("short_profit_factor", 0) or 0) < 1.10:
        recommendation = "SHORTS_DISABLED_NEGATIVE_EDGE"
    if int(historical.get("closed_short_trades", 0) or 0) < 50 and recommendation == "SHORTS_RESEARCH_ONLY":
        recommendation = "SHORTS_DISABLED_INSUFFICIENT_DATA"
    if counts.get("paper_short_eligible", 0):
        recommendation = "SHORTS_ELIGIBLE_FOR_LIMITED_PAPER_TEST"
    return {
        "total": int(len(decisions)) if decisions is not None else 0,
        "counts": counts,
        "historical": historical,
        "inverse_watch_symbols": inverse_symbols,
        "high_squeeze_symbols": high_squeeze,
        "recommendation": recommendation,
    }


def write_short_intelligence_decisions(
    candidates: pd.DataFrame,
    closed_trades: pd.DataFrame | None = None,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    policy: ShortSidePolicy | None = None,
) -> tuple[Path, Path, pd.DataFrame, dict[str, Any]]:
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    decisions = build_short_intelligence_decisions(candidates, closed_trades, policy=policy, generated_at=run_stamp)
    summary = summarize_short_intelligence(decisions, closed_trades)
    csv_path = out_dir / f"short_intelligence_decisions_{run_stamp}.csv"
    md_path = out_dir / f"short_intelligence_decisions_{run_stamp}.md"
    decisions.to_csv(csv_path, index=False)
    counts = summary["counts"]
    historical = summary["historical"]
    md_path.write_text(
        "\n".join(
            [
                "# Short Intelligence Decisions",
                "",
                f"- total_short_candidates: {summary['total']}",
                f"- block_count: {counts.get('block', 0)}",
                f"- research_only_count: {counts.get('research_only', 0)}",
                f"- inverse_watch_count: {counts.get('inverse_watch', 0)}",
                f"- manual_review_count: {counts.get('manual_review', 0)}",
                f"- paper_short_eligible_count: {counts.get('paper_short_eligible', 0)}",
                f"- historical_short_trade_count: {historical.get('closed_short_trades', 0)}",
                f"- historical_short_win_rate: {historical.get('short_win_rate', 0)}",
                f"- historical_short_profit_factor: {historical.get('short_profit_factor', 0)}",
                f"- historical_short_pnl: {historical.get('short_realised_pnl', 0)}",
                f"- worst_short_symbols: {historical.get('worst_short_symbols', '')}",
                f"- top_inverse_watch_symbols: {', '.join(summary['inverse_watch_symbols'])}",
                f"- top_high_squeeze_symbols: {', '.join(summary['high_squeeze_symbols'])}",
                f"- missing_data_summary: {int(decisions['data_quality_status'].eq('insufficient_data').sum()) if not decisions.empty else 0} insufficient, {int(decisions['data_quality_status'].eq('partial').sum()) if not decisions.empty else 0} partial",
                f"- final_short_policy_recommendation: {summary['recommendation']}",
                "",
                "Diagnostics only. No short order submission is enabled by this report.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return csv_path, md_path, decisions, summary


def latest_short_intelligence_inputs(root: Path | str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    base = Path(root) if root else PROJECT_ROOT
    portal = base / "data" / "portal_outputs"
    trading = base / "data" / "trading"
    candidate_files = sorted(
        [*portal.glob("execution_ranked_candidates_*.csv"), *portal.glob("08_alpaca_paper_candidate_pool_*.csv")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    closed_files = sorted(trading.glob("closed_trades_attribution_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = pd.read_csv(candidate_files[0], low_memory=False) if candidate_files else pd.DataFrame()
    closed = pd.read_csv(closed_files[0], low_memory=False) if closed_files else pd.DataFrame()
    return candidates, closed, {"candidate_path": str(candidate_files[0]) if candidate_files else "", "closed_path": str(closed_files[0]) if closed_files else ""}
