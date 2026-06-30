from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, TRADING_DIR, timestamp
from stockml.diagnostics.broker_fill_reconciliation import latest_file, read_csv

ATTRIBUTION_COLUMNS = [
    "trade_id", "symbol", "side", "position_status", "candidate_id", "client_order_id", "cycle_id",
    "candidate_rank", "candidate_status", "order_eligible", "trade_quality_status", "trade_quality_reason",
    "candidate_source", "strategy_mode", "session_mode", "model_version", "model_score", "risk_adjusted_score",
    "expected_trade_return", "meta_label_decision", "meta_label_probability", "overnight_tradable",
    "join_quality", "join_key", "join_warning",
]

CANDIDATE_FIELDS = [
    "cycle_id", "candidate_rank", "candidate_status", "order_eligible", "trade_quality_status", "trade_quality_reason",
    "candidate_source", "strategy_mode", "session_mode", "model_version", "risk_adjusted_score", "expected_trade_return",
    "meta_label_decision", "meta_label_probability", "overnight_tradable",
]


@dataclass(frozen=True)
class CandidateTradeAttributionResult:
    frame: pd.DataFrame
    summary: dict[str, Any]
    report_path: Path | None = None
    summary_path: Path | None = None


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


def _lookup_maps(candidates: pd.DataFrame) -> dict[str, dict[str, dict[str, Any]]]:
    maps = {"candidate_id": {}, "client_order_id": {}, "symbol_cycle": {}, "symbol": {}}
    if candidates.empty:
        return maps
    for row in candidates.fillna("").to_dict("records"):
        candidate_id = _text(row.get("candidate_id"))
        client_order_id = _text(row.get("client_order_id"))
        symbol = _text(row.get("symbol") or row.get("ticker")).upper()
        cycle_id = _text(row.get("cycle_id"))
        if candidate_id:
            maps["candidate_id"][candidate_id] = row
        if client_order_id:
            maps["client_order_id"][client_order_id] = row
        if symbol and cycle_id:
            maps["symbol_cycle"][f"{symbol}:{cycle_id}"] = row
        if symbol and symbol not in maps["symbol"]:
            maps["symbol"][symbol] = row
    return maps


def _match(row: dict[str, Any], maps: dict[str, dict[str, dict[str, Any]]]) -> tuple[dict[str, Any], str, str]:
    candidate_id = _text(row.get("candidate_id"))
    client_order_id = _text(row.get("client_order_id"))
    symbol = _text(row.get("symbol")).upper()
    cycle_id = _text(row.get("cycle_id"))
    if candidate_id and candidate_id in maps["candidate_id"]:
        return maps["candidate_id"][candidate_id], "high", f"candidate_id:{candidate_id}"
    if client_order_id and client_order_id in maps["client_order_id"]:
        return maps["client_order_id"][client_order_id], "high", f"client_order_id:{client_order_id}"
    if symbol and cycle_id and f"{symbol}:{cycle_id}" in maps["symbol_cycle"]:
        return maps["symbol_cycle"][f"{symbol}:{cycle_id}"], "medium", f"symbol_cycle:{symbol}:{cycle_id}"
    if symbol and symbol in maps["symbol"]:
        return maps["symbol"][symbol], "low", f"symbol:{symbol}"
    return {}, "missing", ""


def attribute_trades_to_candidates(ledger: pd.DataFrame, candidates: pd.DataFrame) -> CandidateTradeAttributionResult:
    maps = _lookup_maps(candidates)
    rows = []
    for trade in ledger.fillna("").to_dict("records") if not ledger.empty else []:
        candidate, quality, key = _match(trade, maps)
        warning = "" if quality in {"high", "medium"} else "candidate_context_missing" if quality == "missing" else "symbol_only_fallback"
        out = {
            "trade_id": _text(trade.get("trade_id")),
            "symbol": _text(trade.get("symbol")).upper(),
            "side": _text(trade.get("side")),
            "position_status": _text(trade.get("position_status")),
            "candidate_id": _text(trade.get("candidate_id") or candidate.get("candidate_id")),
            "client_order_id": _text(trade.get("client_order_id") or candidate.get("client_order_id")),
            "cycle_id": _text(trade.get("cycle_id") or candidate.get("cycle_id")),
            "model_score": _text(trade.get("model_score") or candidate.get("model_score") or candidate.get("confidence_score")),
            "join_quality": quality,
            "join_key": key,
            "join_warning": warning,
        }
        for field in CANDIDATE_FIELDS:
            out[field] = _text(trade.get(field) or candidate.get(field))
        rows.append(out)
    frame = pd.DataFrame(rows, columns=ATTRIBUTION_COLUMNS)
    summary = summarize(frame, ledger, candidates)
    return CandidateTradeAttributionResult(frame, summary)


def summarize(frame: pd.DataFrame, ledger: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, Any]:
    if ledger.empty:
        return {"trades": 0, "candidate_rows": int(len(candidates)), "matched_trades": 0, "missing_candidate_context": 0, "status": "insufficient_data"}
    counts = frame["join_quality"].value_counts().to_dict() if not frame.empty else {}
    missing = int(counts.get("missing", 0))
    matched = int(len(frame) - missing)
    return {
        "trades": int(len(ledger)),
        "candidate_rows": int(len(candidates)),
        "matched_trades": matched,
        "missing_candidate_context": missing,
        "high_quality_matches": int(counts.get("high", 0)),
        "medium_quality_matches": int(counts.get("medium", 0)),
        "low_quality_matches": int(counts.get("low", 0)),
        "status": "ok" if missing == 0 else "partial",
    }


def latest_candidate_inputs(root: Path = PROJECT_ROOT) -> tuple[pd.DataFrame, pd.DataFrame]:
    diagnostics = root / "data" / "trading" / "diagnostics"
    portal = root / "data" / "portal_outputs"
    ledger = read_csv(latest_file(diagnostics, "trade_ledger_*.csv"))
    plan = read_csv(latest_file(portal, "08_alpaca_paper_order_plan_*.csv"))
    pool = read_csv(latest_file(portal, "08_alpaca_paper_candidate_pool_*.csv"))
    candidates = pd.concat([frame for frame in [plan, pool] if not frame.empty], ignore_index=True) if (not plan.empty or not pool.empty) else pd.DataFrame()
    return ledger, candidates


def build_latest_candidate_trade_attribution(root: Path = PROJECT_ROOT) -> CandidateTradeAttributionResult:
    ledger, candidates = latest_candidate_inputs(root)
    return attribute_trades_to_candidates(ledger, candidates)


def write_candidate_trade_attribution(result: CandidateTradeAttributionResult, output_dir: Path | str = TRADING_DIR / "diagnostics") -> CandidateTradeAttributionResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    report = out / f"candidate_trade_attribution_{stamp}.csv"
    summary = out / f"candidate_trade_attribution_summary_{stamp}.md"
    result.frame.to_csv(report, index=False)
    summary.write_text("# Candidate-To-Trade Attribution\n\n" + "\n".join(f"- {k}: {v}" for k, v in result.summary.items()) + "\n", encoding="utf-8")
    return CandidateTradeAttributionResult(result.frame, result.summary, report, summary)
