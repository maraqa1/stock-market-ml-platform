from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.trading.config import AlpacaConfig, alpaca_config


EXPERIMENT_MODE = "raw_candidate_no_gates"


@dataclass(frozen=True)
class RawCandidateExperimentConfig:
    enabled: bool = False
    paper_only: bool = True
    live_trading_allowed: bool = False
    max_trades_per_day: int = 3
    max_trades_per_cycle: int = 1
    max_notional_per_trade: float = 250.0
    max_total_experiment_notional: float = 750.0
    daily_loss_stop_usd: float = 50.0
    allow_shorts: bool = False
    allow_no_decision_rows: bool = True
    allow_rejected_candidates: bool = True
    allow_research_only_candidates: bool = True
    require_manual_enable_each_day: bool = True
    separate_ledger: bool = True
    tag_all_orders: bool = True
    allow_same_symbol_as_normal_position: bool = False


@dataclass(frozen=True)
class ExperimentPolicyDecision:
    allowed: bool
    reason: str


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def project_root(root: Path | None = None) -> Path:
    return Path(root).resolve() if root else Path(__file__).resolve().parents[3]


def load_raw_candidate_experiment_config(root: Path | None = None) -> RawCandidateExperimentConfig:
    path = project_root(root) / "config" / "raw_candidate_experiment.yaml"
    payload: dict[str, Any] = {}
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    values = payload.get("raw_candidate_experiment", {}) if isinstance(payload, dict) else {}
    return RawCandidateExperimentConfig(
        enabled=_bool(values.get("enabled"), False),
        paper_only=_bool(values.get("paper_only"), True),
        live_trading_allowed=_bool(values.get("live_trading_allowed"), False),
        max_trades_per_day=max(0, int(values.get("max_trades_per_day", 3) or 0)),
        max_trades_per_cycle=max(0, int(values.get("max_trades_per_cycle", 1) or 0)),
        max_notional_per_trade=max(0.0, float(values.get("max_notional_per_trade", 250) or 0)),
        max_total_experiment_notional=max(0.0, float(values.get("max_total_experiment_notional", 750) or 0)),
        daily_loss_stop_usd=max(0.0, float(values.get("daily_loss_stop_usd", 50) or 0)),
        allow_shorts=_bool(values.get("allow_shorts"), False),
        allow_no_decision_rows=_bool(values.get("allow_no_decision_rows"), True),
        allow_rejected_candidates=_bool(values.get("allow_rejected_candidates"), True),
        allow_research_only_candidates=_bool(values.get("allow_research_only_candidates"), True),
        require_manual_enable_each_day=_bool(values.get("require_manual_enable_each_day"), True),
        separate_ledger=_bool(values.get("separate_ledger"), True),
        tag_all_orders=_bool(values.get("tag_all_orders"), True),
        allow_same_symbol_as_normal_position=_bool(values.get("allow_same_symbol_as_normal_position"), False),
    )


def experiment_dir(root: Path | None = None) -> Path:
    path = project_root(root) / "data" / "trading" / "experiments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manual_enable_path(root: Path | None = None, run_date: date | None = None) -> Path:
    day = run_date or datetime.now(timezone.utc).date()
    return experiment_dir(root) / f"raw_candidate_experiment_enable_{day:%Y%m%d}.flag"


def trades_ledger_path(root: Path | None = None, run_date: date | None = None) -> Path:
    day = run_date or datetime.now(timezone.utc).date()
    return experiment_dir(root) / f"raw_candidate_experiment_trades_{day:%Y%m%d}.csv"


def events_ledger_path(root: Path | None = None, run_date: date | None = None) -> Path:
    day = run_date or datetime.now(timezone.utc).date()
    return experiment_dir(root) / f"raw_candidate_experiment_events_{day:%Y%m%d}.csv"


def read_ledger(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def policy_can_start(
    config: RawCandidateExperimentConfig,
    *,
    root: Path | None = None,
    run_date: date | None = None,
    trade_config: AlpacaConfig | None = None,
    dry_run: bool = False,
) -> ExperimentPolicyDecision:
    trade_config = trade_config or alpaca_config()
    if trade_config.live_trading_enabled or config.live_trading_allowed:
        return ExperimentPolicyDecision(False, "live_trading_disabled_required")
    if config.paper_only and not trade_config.paper_trading_enabled:
        return ExperimentPolicyDecision(False, "paper_trading_required")
    if not config.enabled:
        return ExperimentPolicyDecision(False, "experiment_disabled")
    if config.require_manual_enable_each_day and not dry_run and not manual_enable_path(root, run_date).exists():
        return ExperimentPolicyDecision(False, "manual_daily_enable_missing")
    return ExperimentPolicyDecision(True, "allowed")


def realised_pnl_today(root: Path | None = None, run_date: date | None = None) -> float:
    trades = read_ledger(trades_ledger_path(root, run_date))
    if trades.empty or "realized_pnl" not in trades.columns:
        return 0.0
    return float(pd.to_numeric(trades["realized_pnl"], errors="coerce").fillna(0).sum())


def trades_count_today(root: Path | None = None, run_date: date | None = None) -> int:
    trades = read_ledger(trades_ledger_path(root, run_date))
    return int(len(trades))


def total_notional_today(root: Path | None = None, run_date: date | None = None) -> float:
    trades = read_ledger(trades_ledger_path(root, run_date))
    if trades.empty or "notional" not in trades.columns:
        return 0.0
    return float(pd.to_numeric(trades["notional"], errors="coerce").fillna(0).sum())


def candidate_policy_decision(
    row: pd.Series,
    config: RawCandidateExperimentConfig,
    *,
    root: Path | None = None,
    run_date: date | None = None,
    cycle_selected: int = 0,
    open_experiment_symbols: set[str] | None = None,
    open_normal_symbols: set[str] | None = None,
) -> ExperimentPolicyDecision:
    symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
    side = str(row.get("experiment_side") or "").strip().lower()
    notional = float(row.get("notional") or 0)
    status = str(row.get("original_status") or "").strip().lower()
    trade_action = str(row.get("original_trade_action") or "").strip().lower()
    open_experiment_symbols = open_experiment_symbols or set()
    open_normal_symbols = open_normal_symbols or set()

    if not symbol:
        return ExperimentPolicyDecision(False, "missing_symbol")
    if side == "sell" and not config.allow_shorts:
        return ExperimentPolicyDecision(False, "experiment_skip_short_disabled")
    if trade_action in {"no decision", "no_decision", "neutral", ""} and not config.allow_no_decision_rows:
        return ExperimentPolicyDecision(False, "no_decision_rows_disabled")
    if status == "rejected" and not config.allow_rejected_candidates:
        return ExperimentPolicyDecision(False, "rejected_candidates_disabled")
    if status == "research_only" and not config.allow_research_only_candidates:
        return ExperimentPolicyDecision(False, "research_only_candidates_disabled")
    if cycle_selected >= config.max_trades_per_cycle:
        return ExperimentPolicyDecision(False, "max_trades_per_cycle_reached")
    if trades_count_today(root, run_date) + cycle_selected >= config.max_trades_per_day:
        return ExperimentPolicyDecision(False, "max_trades_per_day_reached")
    if total_notional_today(root, run_date) + notional > config.max_total_experiment_notional:
        return ExperimentPolicyDecision(False, "max_total_experiment_notional_reached")
    if realised_pnl_today(root, run_date) <= -abs(config.daily_loss_stop_usd):
        return ExperimentPolicyDecision(False, "daily_loss_stop_reached")
    if notional <= 0:
        return ExperimentPolicyDecision(False, "non_positive_notional")
    if notional > config.max_notional_per_trade:
        return ExperimentPolicyDecision(False, "max_notional_per_trade_exceeded")
    if symbol in open_experiment_symbols:
        return ExperimentPolicyDecision(False, "existing_open_experiment_position")
    if symbol in open_normal_symbols and not config.allow_same_symbol_as_normal_position:
        return ExperimentPolicyDecision(False, "open_normal_strategy_position")
    return ExperimentPolicyDecision(True, "selected")
