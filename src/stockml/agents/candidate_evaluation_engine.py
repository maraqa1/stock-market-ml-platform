from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from stockml.agents.position_decision_engine import _rotation_config
from stockml.common.paths import DATA_DIR, ensure_data_dirs, timestamp
from stockml.intraday import kill_switch
from stockml.intraday.features import Quote
from stockml.intraday.provider import IntradayProvider


CANDIDATE_EVALUATIONS_DIR = DATA_DIR / "trading" / "candidate_evaluations"

EVALUATION_COLUMNS = [
    "symbol",
    "side",
    "candidate_rank",
    "score",
    "current_price",
    "bid",
    "ask",
    "spread_bps",
    "is_held",
    "held_symbol_to_compare",
    "rank_delta_vs_worst_held",
    "score_delta_vs_worst_held",
    "decision",
    "recommended_action",
    "decision_reason",
    "operator_call_text",
    "evaluated_at",
]


def evaluate_candidates(
    candidate_pool: pd.DataFrame,
    open_positions: pd.DataFrame,
    *,
    quote_loader: Callable[[str], Any] | None = None,
    now: datetime | None = None,
    max_open_positions: int = 5,
    max_spread_bps: float = 50.0,
) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    pool = candidate_pool.copy() if candidate_pool is not None and not candidate_pool.empty else pd.DataFrame(columns=["symbol"])
    positions = open_positions.copy() if open_positions is not None and not open_positions.empty else pd.DataFrame(columns=["symbol"])
    if pool.empty or "symbol" not in pool.columns:
        return pd.DataFrame(columns=EVALUATION_COLUMNS)

    pool["symbol"] = pool["symbol"].astype(str).str.upper()
    if "candidate_rank" not in pool.columns:
        pool["candidate_rank"] = range(1, len(pool) + 1)
    pool = pool.sort_values("candidate_rank").head(100).copy()

    held_symbols = _held_symbols(positions)
    held_context = _held_context(pool, held_symbols)
    cfg = _rotation_config()
    min_rank_delta = int(cfg["min_rank_improvement"])
    min_score_delta = float(cfg["min_score_delta"])
    open_slots = max(0, max_open_positions - len(held_symbols))
    loader = quote_loader or _alpaca_quote_loader()

    rows: list[dict[str, Any]] = []
    for _, candidate in pool.iterrows():
        symbol = str(candidate.get("symbol") or "").upper()
        quote = _safe_quote(loader, symbol)
        price = _quote_price(quote) or _num(candidate.get("current_price"))
        bid = _quote_field(quote, "bid")
        ask = _quote_field(quote, "ask")
        spread_bps = _spread_bps(bid, ask)
        rank = int(_num(candidate.get("candidate_rank")) or 0)
        score = _score(candidate)
        side = _side(candidate)
        is_held = symbol in held_symbols
        worst = held_context.get(side) or {}
        rank_delta = _num(worst.get("candidate_rank")) - rank if worst else 0.0
        score_delta = score - _num(worst.get("score")) if worst else 0.0
        decision, action, reason, call = _candidate_decision(
            candidate,
            price=price,
            is_held=is_held,
            spread_bps=spread_bps,
            max_spread_bps=max_spread_bps,
            open_slots=open_slots,
            rank_delta=rank_delta,
            score_delta=score_delta,
            min_rank_delta=min_rank_delta,
            min_score_delta=min_score_delta,
            has_held_comparison=bool(worst),
        )
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "candidate_rank": rank,
                "score": score,
                "current_price": price,
                "bid": bid,
                "ask": ask,
                "spread_bps": spread_bps,
                "is_held": is_held,
                "held_symbol_to_compare": worst.get("symbol", ""),
                "rank_delta_vs_worst_held": rank_delta,
                "score_delta_vs_worst_held": score_delta,
                "decision": decision,
                "recommended_action": action,
                "decision_reason": reason,
                "operator_call_text": call,
                "evaluated_at": now.isoformat(timespec="seconds"),
            }
        )
    return pd.DataFrame(rows, columns=EVALUATION_COLUMNS)


def write_candidate_evaluations(evaluations: pd.DataFrame, stamp: str | None = None) -> Path:
    ensure_data_dirs()
    CANDIDATE_EVALUATIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = CANDIDATE_EVALUATIONS_DIR / f"candidate_evaluation_{stamp or timestamp()}.csv"
    evaluations.to_csv(path, index=False)
    return path


def _candidate_decision(
    candidate: pd.Series,
    *,
    price: float,
    is_held: bool,
    spread_bps: float,
    max_spread_bps: float,
    open_slots: int,
    rank_delta: float,
    score_delta: float,
    min_rank_delta: int,
    min_score_delta: float,
    has_held_comparison: bool,
) -> tuple[str, str, str, str]:
    status = str(candidate.get("trade_quality_status") or "").lower()
    eligible = bool(candidate.get("order_eligible", False))
    if is_held:
        return "watch", "already_held", "already_held", "Already held; monitor as an open position."
    if price <= 0:
        return "skip", "skip_candidate", "price_unavailable", "No current price; keep out of Action Queue."
    if spread_bps <= 0:
        return "skip", "skip_candidate", "spread_unavailable", "No reliable bid/ask spread; keep out of Action Queue."
    if spread_bps > max_spread_bps:
        return "skip", "skip_candidate", "wide_spread", "Spread too wide for candidate entry."
    if status not in {"approved", "reduced"} or not eligible:
        return "skip", "skip_candidate", "risk_or_quality_rejected", "Risk/quality gate rejected this candidate."
    if open_slots > 0:
        return "open_candidate", "review_open_candidate", "candidate_slot_available", "Review candidate for possible paper entry."
    if has_held_comparison and rank_delta >= min_rank_delta and score_delta >= min_score_delta:
        return "replace_candidate", "review_replacement_candidate", "candidate_better_than_held", "Review candidate against weakest held position."
    return "watch", "defer_candidate", "no_portfolio_slot_or_material_improvement", "No slot or material improvement; wait for next evaluation."


def _alpaca_quote_loader() -> Callable[[str], Quote]:
    verdict = kill_switch.gate(action="evaluate")
    if not verdict.allow:
        raise RuntimeError(f"candidate_evaluation_blocked_by_kill_switch:{','.join(verdict.tripped)}")
    provider = IntradayProvider()
    return provider.fetch_quote


def _safe_quote(loader: Callable[[str], Any], symbol: str) -> Any:
    try:
        return loader(symbol)
    except Exception:
        return None


def _held_symbols(open_positions: pd.DataFrame) -> set[str]:
    if open_positions.empty or "symbol" not in open_positions.columns:
        return set()
    frame = open_positions.copy()
    if "status" in frame.columns:
        status = frame["status"].fillna("open").astype(str).str.lower()
        frame = frame[status.eq("open") | status.eq("")]
    return {str(symbol).upper() for symbol in frame["symbol"].dropna()}


def _held_context(pool: pd.DataFrame, held_symbols: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    held = pool[pool["symbol"].isin(held_symbols)].copy()
    if held.empty:
        return out
    for _, row in held.iterrows():
        side = _side(row)
        current = out.get(side)
        row_data = {"symbol": row.get("symbol"), "candidate_rank": _num(row.get("candidate_rank")), "score": _score(row)}
        if current is None or row_data["candidate_rank"] > _num(current.get("candidate_rank")):
            out[side] = row_data
    return out


def _score(row: pd.Series) -> float:
    for column in ["score", "risk_adjusted_score", "confidence_score", "side_probability"]:
        value = _num(row.get(column))
        if value:
            return value
    return 0.0


def _side(row: pd.Series) -> str:
    raw = str(row.get("trade_action") or row.get("side") or "").lower()
    return "short" if raw in {"short", "sell"} else "long"


def _quote_price(quote: Any) -> float:
    last = _quote_field(quote, "last_price")
    bid = _quote_field(quote, "bid")
    ask = _quote_field(quote, "ask")
    if last:
        return last
    if bid and ask:
        return (bid + ask) / 2
    return bid or ask or 0.0


def _quote_field(quote: Any, name: str) -> float:
    if quote is None:
        return 0.0
    if isinstance(quote, dict):
        return _num(quote.get(name))
    return _num(getattr(quote, name, None))


def _spread_bps(bid: float, ask: float) -> float:
    if bid <= 0 or ask <= 0:
        return 0.0
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 10000 if mid else 0.0


def _num(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else float(parsed)
