from __future__ import annotations

from pathlib import Path
from typing import Optional

from portal.services.database_reader import latest_gold_for_ticker, model_artifacts, price_history_for_ticker
from portal.services.latest_file_reader import file_status, latest_file, latest_row_by_value, readable_reason, safe_read_csv


def stock_detail_context(ticker: str, root: Optional[Path] = None) -> dict:
    clean = str(ticker).upper().strip()
    gold_file = latest_file(root, "gold", "06_us_gold_ml_dataset_*.csv")
    signal_file = latest_file(root, "model_outputs", "advanced_model_signal_table_*.csv", fallback_keys=["portal_outputs"])
    price_file = project_price_file(root)
    latest = latest_gold_for_ticker(clean) if root is None else {}
    db_signals = model_artifacts("signal_table", limit=10000) if root is None else safe_read_csv(signal_file)
    using_db = root is None and (bool(latest) or not db_signals.empty)

    if not latest:
        latest = latest_row_by_value(gold_file, "ticker", clean)

    signals = db_signals if not db_signals.empty else safe_read_csv(signal_file)
    if not signals.empty and "ticker" in signals.columns:
        sig = signals[signals["ticker"].astype(str).str.upper().eq(clean)].copy()
        if not sig.empty:
            latest.update(sig.iloc[-1].to_dict())
    if latest:
        reason = latest.get("signal_reason", latest.get("no_decision_reason", latest.get("reason", "")))
        latest["reason_readable"] = readable_reason(reason)

    price_rows = price_history_for_ticker(clean, limit=50) if root is None else []
    if not price_rows:
        prices = safe_read_csv(price_file)
        if not prices.empty and "ticker" in prices.columns:
            price_rows = prices[prices["ticker"].astype(str).str.upper().eq(clean)].tail(50).to_dict("records")

    return {
        "ticker": clean,
        "latest": latest,
        "price_rows": price_rows,
        "files": [file_status(gold_file, "Gold dataset"), file_status(signal_file, "Signal table"), file_status(price_file, "Price history")],
        "data_source": "PostgreSQL" if using_db else "CSV",
    }


def project_price_file(root: Optional[Path] = None):
    base = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
    path = base / "data" / "raw" / "03_us_price_history_store.csv"
    return path if path.exists() else None
