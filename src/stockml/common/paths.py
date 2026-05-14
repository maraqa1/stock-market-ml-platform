from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
GOLD_DIR = DATA_DIR / "gold"
MODEL_OUTPUTS_DIR = DATA_DIR / "model_outputs"
PORTAL_OUTPUTS_DIR = DATA_DIR / "portal_outputs"
TRADING_DIR = DATA_DIR / "trading"
PAPER_ORDERS_DIR = TRADING_DIR / "paper_orders"
PAPER_FILLS_DIR = TRADING_DIR / "paper_fills"
PAPER_POSITIONS_DIR = TRADING_DIR / "paper_positions"
PAPER_TRADE_JOURNAL_DIR = TRADING_DIR / "paper_trade_journal"
PAPER_PNL_DIR = TRADING_DIR / "paper_pnl"
AGENT_DECISIONS_DIR = TRADING_DIR / "agent_decisions"
EXECUTION_REPORTS_DIR = TRADING_DIR / "execution_reports"
OPERATOR_ACTIONS_DIR = TRADING_DIR / "operator_actions"
PER_SYMBOL_FORECAST_DIR = TRADING_DIR / "per_symbol_forecast"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_data_dirs() -> None:
    for path in [
        RAW_DIR,
        INTERIM_DIR,
        PROCESSED_DIR,
        GOLD_DIR,
        MODEL_OUTPUTS_DIR,
        PORTAL_OUTPUTS_DIR,
        PAPER_ORDERS_DIR,
        PAPER_FILLS_DIR,
        PAPER_POSITIONS_DIR,
        PAPER_TRADE_JOURNAL_DIR,
        PAPER_PNL_DIR,
        AGENT_DECISIONS_DIR,
        EXECUTION_REPORTS_DIR,
        OPERATOR_ACTIONS_DIR,
        PER_SYMBOL_FORECAST_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def latest_file(directory: Path, pattern: str) -> Optional[Path]:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
