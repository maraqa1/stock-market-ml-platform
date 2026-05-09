from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from portal.services.latest_file_reader import latest_file, safe_read_csv
from portal.services.trading_api_service import position_lineage_context
from stockml.db.connection import get_engine
from stockml.services.events import position_id_for_symbol


COMMON_REFERENCE = {
    "AAPL": "Apple Inc.",
    "AMZN": "Amazon.com, Inc.",
    "GOOG": "Alphabet Inc.",
    "GOOGL": "Alphabet Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "TSLA": "Tesla, Inc.",
}


def _engine():
    return get_engine(required=False)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, (dict, list)):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        number = float(value)
        if pd.isna(number):
            return None
        return number
    except Exception:
        return None


def _timestamp(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _optional_latest(root: Path, key: str, pattern: str) -> Path | None:
    try:
        return latest_file(root, key, pattern)
    except KeyError:
        return None


def _db_row(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    if engine is None:
        return {}
    try:
        with engine.connect() as conn:
            row = conn.execute(text(sql), params).mappings().first()
        return {str(key): _json_value(value) for key, value in dict(row).items()} if row else {}
    except Exception:
        return {}


def _db_rows(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    engine = _engine()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [{str(key): _json_value(value) for key, value in dict(row).items()} for row in rows]
    except Exception:
        return []


def _security_from_artifacts(root: Path, symbol: str) -> dict[str, Any]:
    for key, pattern, column in [
        ("portal_outputs", "08_alpaca_paper_candidate_pool_*.csv", "symbol"),
        ("gold", "06_us_gold_ml_dataset_*.csv", "ticker"),
        ("model_outputs", "advanced_model_signal_table_*.csv", "ticker"),
        ("portal_outputs", "08_alpaca_paper_order_plan_*.csv", "symbol"),
        ("portal_outputs", "08_alpaca_paper_positions_*.csv", "symbol"),
    ]:
        frame = safe_read_csv(latest_file(root, key, pattern), nrows=5000)
        if frame.empty or column not in frame.columns:
            continue
        rows = frame[frame[column].fillna("").astype(str).str.upper().eq(symbol)]
        if rows.empty:
            continue
        row = rows.tail(1).iloc[0].to_dict()
        return {
            "symbol": symbol,
            "name": row.get("company") or row.get("name") or symbol,
            "sector": row.get("sector") or "Unknown",
            "industry": row.get("industry") or "",
            "market_cap": _float(row.get("market_cap")),
        }
    return {}


def _security(root: Path, symbol: str) -> dict[str, Any]:
    row = _db_row(
        """
        select symbol, name, sector, industry, market_cap_band
        from dim_security
        where upper(symbol) = :symbol
        limit 1
        """,
        {"symbol": symbol},
    )
    if row:
        return {
            "symbol": symbol,
            "name": row.get("name") or symbol,
            "sector": " / ".join(part for part in [row.get("sector"), row.get("industry")] if part) or "Unknown",
            "industry": row.get("industry") or "",
            "market_cap": row.get("market_cap_band"),
        }
    artifact_security = _security_from_artifacts(root, symbol)
    if artifact_security:
        return artifact_security
    if symbol in COMMON_REFERENCE:
        return {
            "symbol": symbol,
            "name": COMMON_REFERENCE[symbol],
            "sector": "Reference",
            "industry": "",
            "market_cap": None,
        }
    return {}


def _position(root: Path, symbol: str) -> dict[str, Any] | None:
    row = _db_row(
        """
        select id, symbol, side, qty, entry_price, opened_at, status
        from positions
        where upper(symbol) = :symbol and closed_at is null
        order by opened_at desc nulls last
        limit 1
        """,
        {"symbol": symbol},
    )
    if row:
        return {
            "id": row.get("id") or position_id_for_symbol(symbol),
            "side": row.get("side") or "long",
            "qty": _float(row.get("qty")) or 0,
            "entry": _float(row.get("entry_price")) or 0,
            "pnl_dollars": 0,
            "pnl_pct": 0,
            "age_days": "",
            "status": row.get("status") or "open",
        }

    frame = safe_read_csv(latest_file(root, "portal_outputs", "08_alpaca_paper_positions_*.csv"), nrows=1000)
    if frame.empty or "symbol" not in frame.columns:
        return None
    rows = frame[frame["symbol"].fillna("").astype(str).str.upper().eq(symbol)]
    if rows.empty:
        return None
    data = rows.tail(1).iloc[0].to_dict()
    qty = _float(data.get("qty")) or 0
    cost_basis = _float(data.get("cost_basis")) or 0
    return {
        "id": position_id_for_symbol(symbol),
        "side": str(data.get("side") or "long").lower(),
        "qty": qty,
        "entry": _float(data.get("avg_entry_price")) or (cost_basis / qty if qty else 0),
        "pnl_dollars": _float(data.get("unrealized_pl")) or 0,
        "pnl_pct": _float(data.get("unrealized_plpc")) or 0,
        "age_days": data.get("age_days") or "",
        "status": data.get("status") or "open",
        "market_value": _float(data.get("market_value")) or 0,
    }


def _today_signal(root: Path, symbol: str) -> dict[str, Any] | None:
    row = _db_row(
        """
        select rank, bias, score, expected_edge, sector, in_basket, excluded_reason
        from shortlist_snapshots
        where upper(symbol) = :symbol
          and run_id = (select max(run_id) from pipeline_runs)
        order by rank asc
        limit 1
        """,
        {"symbol": symbol},
    )
    if not row:
        frame = safe_read_csv(latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"), nrows=1000)
        if frame.empty or "symbol" not in frame.columns:
            return None
        rows = frame[frame["symbol"].fillna("").astype(str).str.upper().eq(symbol)]
        if rows.empty:
            return None
        data = rows.tail(1).iloc[0].to_dict()
        rank = int(_float(data.get("candidate_rank")) or 0)
        score = _float(data.get("risk_adjusted_score")) or _float(data.get("model_score")) or 0
        expected = _float(data.get("expected_trade_return")) or 0
        row = {
            "rank": rank,
            "bias": str(data.get("trade_action") or data.get("side") or "neutral").lower(),
            "score": score,
            "expected_edge": expected,
            "sector": data.get("sector") or "",
            "in_basket": bool(data.get("order_eligible")) if "order_eligible" in data else False,
            "excluded_reason": data.get("trade_quality_reason") or data.get("no_decision_reason") or "",
        }
    rank = int(_float(row.get("rank")) or 0)
    return {
        "rank": rank,
        "of": _candidate_count(root),
        "bias": row.get("bias") or "neutral",
        "score": _float(row.get("score")) or 0,
        "expected_edge_pct": _float(row.get("expected_edge")) or 0,
        "in_basket": bool(row.get("in_basket")),
        "excluded_reason": row.get("excluded_reason") or "",
        "top_features": _top_features(root, symbol),
    }


def _candidate_count(root: Path) -> int:
    frame = safe_read_csv(latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"), nrows=1000)
    return int(len(frame))


def _top_features(root: Path, symbol: str) -> list[dict[str, Any]]:
    frame = safe_read_csv(latest_file(root, "model_outputs", "advanced_model_feature_importance_*.csv"), nrows=50)
    if frame.empty:
        return []
    name_col = "feature_name" if "feature_name" in frame.columns else frame.columns[0]
    value_col = "importance" if "importance" in frame.columns else frame.columns[-1]
    rows = frame.head(3).to_dict("records")
    return [
        {"name": str(row.get(name_col) or ""), "contribution": _float(row.get(value_col)) or 0}
        for row in rows
    ]


def _latest_price(root: Path, symbol: str) -> dict[str, Any]:
    for key, pattern, column in [
        ("portal_outputs", "08_alpaca_paper_positions_*.csv", "symbol"),
        ("portal_outputs", "08_alpaca_paper_candidate_pool_*.csv", "symbol"),
        ("gold", "06_us_gold_ml_dataset_*.csv", "ticker"),
        ("raw", "03_us_price_history_store*.csv", "ticker"),
    ]:
        frame = safe_read_csv(latest_file(root, key, pattern), nrows=5000)
        if frame.empty or column not in frame.columns:
            continue
        rows = frame[frame[column].fillna("").astype(str).str.upper().eq(symbol)]
        if rows.empty:
            continue
        data = rows.tail(1).iloc[0].to_dict()
        last = _float(data.get("current_price")) or _float(data.get("close")) or _float(data.get("adj_close")) or _float(data.get("lastday_price"))
        prev = _float(data.get("lastday_price")) or _float(data.get("prev_close"))
        change = _float(data.get("change_today"))
        if change is None and last is not None and prev:
            change = last / prev - 1
        return {
            "last_price": last,
            "change_today_pct": change or 0,
            "volume": _float(data.get("volume")) or _float(data.get("intraday_volume")) or 0,
            "market_cap": _float(data.get("market_cap")),
        }
    return {"last_price": None, "change_today_pct": 0, "volume": 0, "market_cap": None}


def _freshness(root: Path) -> dict[str, Any]:
    return {
        "eod_prices": _timestamp(latest_file(root, "raw", "03_us_price_history_store*.csv")),
        "features": _timestamp(latest_file(root, "gold", "06_us_gold_ml_dataset_*.csv")),
        "sentiment": _timestamp(_optional_latest(root, "sentiment", "*.csv")),
        "filings": _timestamp(_optional_latest(root, "sec_filings", "*.csv")),
        "earnings_next": "",
    }


def _history(root: Path, symbol: str) -> dict[str, list[dict[str, Any]]]:
    scores: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    pnl: list[dict[str, Any]] = []
    frame = safe_read_csv(latest_file(root, "model_outputs", "advanced_model_signal_table_*.csv"), nrows=10000)
    if not frame.empty and "ticker" in frame.columns:
        rows = frame[frame["ticker"].fillna("").astype(str).str.upper().eq(symbol)].tail(30)
        for index, row in enumerate(rows.to_dict("records")):
            day = str(row.get("date") or row.get("prediction_date") or index + 1)
            scores.append({"date": day, "value": _float(row.get("risk_adjusted_score")) or _float(row.get("model_score")) or 0})
            signals.append({"date": day, "bias": str(row.get("trade_action") or "neutral").lower(), "in_basket": False})
    position = _position(root, symbol)
    if position:
        pnl.append({"date": "current", "value": position.get("pnl_pct") or 0})
    return {"scores": scores[-30:], "signals": signals[-30:], "pnl": pnl[-30:]}


def _events(root: Path, symbol: str) -> list[dict[str, Any]]:
    position_id = position_id_for_symbol(symbol)
    return list(reversed(position_lineage_context(root, position_id)["events"]))[:50]


def get(symbol: str, root: Path) -> dict[str, Any] | None:
    clean = str(symbol or "").strip().upper()
    if not clean:
        return None
    security = _security(root, clean)
    position = _position(root, clean)
    today_signal = _today_signal(root, clean)
    events = _events(root, clean)
    if not security and not position and not today_signal and not events:
        return None
    price = _latest_price(root, clean)
    market_cap = price.get("market_cap") or security.get("market_cap")
    return {
        "symbol": clean,
        "name": security.get("name") or clean,
        "sector": security.get("sector") or "Unknown",
        "last_price": price.get("last_price"),
        "change_today_pct": price.get("change_today_pct") or 0,
        "volume": price.get("volume") or 0,
        "market_cap": market_cap,
        "position": position,
        "today_signal": today_signal,
        "freshness": _freshness(root),
        "history": _history(root, clean),
        "events": events,
    }
