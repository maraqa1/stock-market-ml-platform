from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

import yaml
import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from stockml.autopilot.basket_risk import evaluate_basket_risk
from stockml.autopilot.candidate_arbitration import arbitrate_candidates, arbitration_score
from stockml.common.paths import HOLDING_PERIOD_DIR, PORTAL_OUTPUTS_DIR, PROJECT_ROOT, TRADING_DIR, latest_file
from stockml.db.connection import get_engine
from stockml.db.schema import autopilot_open_log, intraday_candidate_snapshots, intraday_promotion_log
from stockml.intraday.provider import IntradayProvider
from stockml.intraday import kill_switch
from stockml.services.events import position_id_for_symbol, record_event_once
from stockml.safety.paper_only_guard import paper_only_guard
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config
from stockml.trading.anti_churn_guard import AntiChurnConfig, guard_actions, load_recent_trade_history, write_anti_churn_report
from stockml.trading.activity_journal import enrich_activity_details
from stockml.trading.execution_engine import submit_paper_order_payload
from stockml.trading.gate_override import paper_allow_all_override_active
from stockml.trading.lifecycle_ids import LINEAGE_FIELDS, candidate_lineage, order_lineage, stable_id
from stockml.trading.order_builder import extended_limit_price, validate_order_payload
from stockml.trading.position_intent_guard import PositionIntentConfig, guard_order_submission, record_position_intent_block, write_position_intent_report
from stockml.trading.session_mode import classify_session_mode
from stockml.trading.session_order_policy import session_order_policy
from stockml.trading.signal_alignment_gate import evaluate_entry_signal_alignment
from stockml.trading.submission_guards import asset_is_overnight_tradable


CONFIG_PATH = PROJECT_ROOT / "config" / "autopilot.yaml"
MAX_PORTAL_LIMIT = 100
MODEL_EVIDENCE_FIELDS = (
    "trade_action",
    "directional_action",
    "directional_strength",
    "side_probability",
    "risk_adjusted_score",
    "meta_label_decision",
    "trade_quality_status",
    "candidate_status",
    "order_eligible",
    "strategy_stream",
    "trading_stream",
    "same_day_momentum",
    "current_trade_action",
    "same_day_trade_action",
    "nightly_bias",
    "probability_edge",
    "expected_trade_return",
    "rank_overall",
    "candidate_rank_overall",
)


@dataclass(frozen=True)
class AutoOpenConfig:
    open_enabled: bool = False
    rotate_enabled: bool = False
    max_auto_opens_per_day: int = 3
    max_positions: int = 20
    min_account_equity_usd: float = 250.0
    min_position_value_usd: float = 50.0
    max_single_position_pct_of_equity: float = 0.20
    default_position_pct_of_equity: float = 0.10
    default_position_value_cap_usd: float = 2500.0
    flat_account_fallback_enabled: bool = True
    flat_account_fallback_min_score: float = 0.40
    flat_account_fallback_max_per_day: int = 1
    flat_account_fallback_size_multiplier: float = 0.50
    near_miss_fallback_enabled: bool = True
    near_miss_fallback_requires_flat_account: bool = False
    near_miss_fallback_max_per_day: int = 5
    near_miss_fallback_size_multiplier: float = 0.75
    near_miss_fallback_max_distance_pct: float = 0.25
    near_miss_fallback_max_file_age_minutes: int = 30
    near_miss_fallback_allowed_severities: tuple[str, ...] = ("near_miss", "moderate_gap")
    near_miss_fallback_allowed_gates: tuple[str, ...] = (
        "risk_adjusted_score_below_threshold",
        "market_cap_below_minimum",
        "volatility_extreme",
    )
    near_miss_fallback_block_hard_fail_gates: tuple[str, ...] = (
        "price_below_minimum",
        "market_cap_below_minimum",
        "liquidity_below_minimum",
        "volatility_extreme",
    )
    per_symbol_forecast_fallback_enabled: bool = True
    per_symbol_forecast_fallback_max_per_day: int = 5
    per_symbol_forecast_fallback_size_multiplier: float = 0.75
    per_symbol_forecast_fallback_max_file_age_minutes: int = 30
    per_symbol_forecast_fallback_min_confirmation_score: float = 80.0
    per_symbol_forecast_fallback_min_profitability_score: float = 0.0
    per_symbol_forecast_fallback_max_profitability_score: float = 300.0
    per_symbol_forecast_fallback_allowed_confirmations: tuple[str, ...] = ("confirmed",)
    plan_fallback_enabled: bool = True
    plan_fallback_size_multiplier: float = 1.0
    plan_fallback_max_file_age_minutes: int = 720
    same_day_momentum_enabled: bool = True
    same_day_momentum_max_per_day: int = 8
    same_day_momentum_size_multiplier: float = 0.50
    same_day_momentum_min_score: float = 0.55
    same_day_momentum_max_spread_bps: float = 35.0
    same_day_momentum_min_dollar_volume: float = 500_000.0
    holding_review_gate_enabled: bool = True
    holding_review_allow_watch: bool = True
    max_position_loss_pct: float = 2.0
    signal_flip_confirmation_clocks: int = 2
    stale_unknown_loss_close_pct: float = 2.0
    trailing_profit_arm_pct: float = 2.0
    trailing_profit_giveback_pct: float = 1.0
    basket_drawdown_pause_pct: float = 1.5
    max_long_positions: int = 15
    max_short_positions: int = 15
    near_miss_entries_with_open_positions: bool = False
    close_automation_mode: str = "automatic"
    validation_mode: bool = False
    validation_max_new_orders_per_cycle: int = 1
    validation_max_new_orders_per_day: int = 2
    validation_max_open_positions_total: int = 3


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _bool_flag(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def per_symbol_forecast_quality_block_reason(details: dict[str, Any], cfg: AutoOpenConfig) -> str:
    profitability = _optional_float(details.get("expected_profitability_score"))
    if profitability is None:
        return "profitability_not_evaluated"
    if profitability < cfg.per_symbol_forecast_fallback_min_profitability_score:
        return "profitability_below_minimum"
    if profitability > cfg.per_symbol_forecast_fallback_max_profitability_score:
        return "profitability_score_out_of_range"
    for key, reason in (
        ("profitability_ok", "profitability_not_confirmed"),
        ("risk_reward_ok", "risk_reward_not_confirmed"),
        ("liquidity_ok", "liquidity_not_confirmed"),
        ("volatility_ok", "volatility_not_confirmed"),
    ):
        flag = _bool_flag(details.get(key))
        if flag is None:
            return f"{key}_not_evaluated"
        if flag is not True:
            return reason
    return ""


def model_evidence_block_reason(candidate: dict[str, Any], details: dict[str, Any]) -> str:
    evidence = {**candidate, **details}
    if _is_same_day_momentum_candidate(evidence):
        return ""
    meta_decision = str(evidence.get("meta_label_decision") or "").strip().lower()
    if meta_decision and "skip" in meta_decision:
        return "model_meta_label_rejected"

    trade_quality = str(evidence.get("trade_quality_status") or "").strip().lower()
    if trade_quality == "rejected":
        return "model_trade_quality_rejected"

    candidate_status = str(evidence.get("candidate_status") or "").strip().lower()
    if candidate_status == "rejected":
        return "model_candidate_rejected"

    order_eligible = _bool_flag(evidence.get("order_eligible"))
    if order_eligible is False:
        return "model_order_ineligible"
    if order_eligible is True:
        return ""

    if meta_decision in {"take trade", "take_trade", "trade", "approved"}:
        return ""

    directional_action = str(evidence.get("directional_action") or evidence.get("trade_action") or "").strip().lower()
    directional_strength = _optional_float(evidence.get("directional_strength"))
    if directional_action in {"long", "short"} and directional_strength is not None and directional_strength >= 0.97:
        return ""

    if trade_quality in {"approved", "reduced"} and directional_action in {"long", "short"}:
        return ""

    return "model_evidence_missing"


def _is_same_day_momentum_candidate(details: dict[str, Any]) -> bool:
    stream = str(details.get("strategy_stream") or details.get("trading_stream") or "").strip().lower()
    return bool(details.get("same_day_momentum")) or stream == "same_day_momentum"


def same_day_momentum_block_reason(candidate: dict[str, Any], details: dict[str, Any], cfg: AutoOpenConfig) -> str:
    if not cfg.same_day_momentum_enabled:
        return "same_day_momentum_disabled"
    evidence = {**candidate, **details}
    score = _optional_float(evidence.get("promotion_score") or evidence.get("same_day_confidence"))
    if score is None:
        return "same_day_score_missing"
    if score < cfg.same_day_momentum_min_score:
        return "same_day_score_below_minimum"
    spread = _optional_float(evidence.get("spread_bps"))
    if spread is not None and spread > cfg.same_day_momentum_max_spread_bps:
        return "same_day_spread_too_wide"
    dollar_volume = _optional_float(evidence.get("dollar_volume_today") or evidence.get("manual_dollar_traded"))
    if dollar_volume is not None and dollar_volume < cfg.same_day_momentum_min_dollar_volume:
        return "same_day_liquidity_below_minimum"
    action = str(evidence.get("same_day_trade_action") or evidence.get("current_trade_action") or evidence.get("trade_action") or "").strip().lower()
    bias = str(evidence.get("nightly_bias") or "").strip().lower()
    if action not in {"long", "short"} and bias not in {"long", "short"}:
        return "same_day_direction_missing"
    return ""


def _holding_review_dir(root: Path | str | None = None) -> Path:
    if root is None:
        return HOLDING_PERIOD_DIR
    return Path(root) / "data" / "trading" / "holding_period"


def latest_holding_review_map(root: Path | str | None = None) -> dict[str, dict[str, Any]]:
    path = latest_file(_holding_review_dir(root), "holding_review_*.csv")
    if path is None or not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return {}
    if frame.empty or "symbol" not in frame.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in frame.fillna("").to_dict("records"):
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol:
            out[symbol] = row
    return out


def holding_review_block_reason(
    symbol: str,
    review: dict[str, Any] | None,
    cfg: AutoOpenConfig,
) -> str:
    if not cfg.holding_review_gate_enabled:
        return ""
    if not review:
        return "holding_review_missing"
    quality = str(review.get("holding_quality") or "").strip().lower()
    passed = _bool_flag(review.get("holding_gate_pass"))
    if quality == "avoid" or passed is False:
        return str(review.get("holding_gate_reason") or "holding_review_blocked")
    if quality == "watch" and not cfg.holding_review_allow_watch:
        return "holding_review_watch_requires_confirmation"
    if quality not in {"strong", "watch"} and passed is not True:
        return "holding_review_not_confirmed"
    return ""


def auto_open_config_path(root: Path | str | None = None) -> Path:
    if root is None:
        return CONFIG_PATH
    return Path(root) / "config" / "autopilot.yaml"


def _default_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "autopilot": {
            "open_enabled": False,
            "rotate_enabled": False,
            "max_auto_opens_per_day": 3,
            "max_positions": 20,
            "min_account_equity_usd": 250,
            "min_position_value_usd": 50,
            "max_single_position_pct_of_equity": 0.20,
            "default_position_pct_of_equity": 0.10,
            "default_position_value_cap_usd": 2500,
            "flat_account_fallback_enabled": True,
            "flat_account_fallback_min_score": 0.40,
            "flat_account_fallback_max_per_day": 1,
            "flat_account_fallback_size_multiplier": 0.50,
            "near_miss_fallback_enabled": True,
            "near_miss_fallback_requires_flat_account": False,
            "near_miss_fallback_max_per_day": 5,
            "near_miss_fallback_size_multiplier": 0.75,
            "near_miss_fallback_max_distance_pct": 0.25,
            "near_miss_fallback_max_file_age_minutes": 30,
            "near_miss_fallback_allowed_severities": ["near_miss", "moderate_gap"],
            "near_miss_fallback_allowed_gates": [
                "risk_adjusted_score_below_threshold",
                "market_cap_below_minimum",
                "volatility_extreme",
            ],
            "near_miss_fallback_block_hard_fail_gates": [
                "price_below_minimum",
                "market_cap_below_minimum",
                "liquidity_below_minimum",
                "volatility_extreme",
            ],
            "per_symbol_forecast_fallback_enabled": True,
            "per_symbol_forecast_fallback_max_per_day": 5,
            "per_symbol_forecast_fallback_size_multiplier": 0.75,
            "per_symbol_forecast_fallback_max_file_age_minutes": 30,
            "per_symbol_forecast_fallback_min_confirmation_score": 80,
            "per_symbol_forecast_fallback_min_profitability_score": 0,
            "per_symbol_forecast_fallback_max_profitability_score": 300,
            "per_symbol_forecast_fallback_allowed_confirmations": ["confirmed"],
            "plan_fallback_enabled": True,
            "plan_fallback_size_multiplier": 1.0,
            "plan_fallback_max_file_age_minutes": 720,
            "same_day_momentum_enabled": True,
            "same_day_momentum_max_per_day": 8,
            "same_day_momentum_size_multiplier": 0.50,
            "same_day_momentum_min_score": 0.55,
            "same_day_momentum_max_spread_bps": 35,
            "same_day_momentum_min_dollar_volume": 500000,
            "holding_review_gate_enabled": True,
            "holding_review_allow_watch": True,
            "max_position_loss_pct": 2.0,
            "signal_flip_confirmation_clocks": 2,
            "stale_unknown_loss_close_pct": 2.0,
            "trailing_profit_arm_pct": 2.0,
            "trailing_profit_giveback_pct": 1.0,
            "basket_drawdown_pause_pct": 1.5,
            "max_long_positions": 15,
            "max_short_positions": 15,
            "near_miss_entries_with_open_positions": True,
            "close_automation_mode": "automatic",
        },
    }


def _read_payload(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_auto_open_config(path: Path | str | None = None, *, root: Path | str | None = None) -> AutoOpenConfig:
    config_path = Path(path) if path is not None else auto_open_config_path(root)
    payload = _read_payload(config_path)
    section = payload.get("autopilot") if isinstance(payload, dict) else {}
    section = section if isinstance(section, dict) else {}
    allowed_gates = section.get("near_miss_fallback_allowed_gates")
    if isinstance(allowed_gates, str):
        allowed_gate_values = tuple(part.strip() for part in allowed_gates.split(",") if part.strip())
    elif isinstance(allowed_gates, list):
        allowed_gate_values = tuple(str(part).strip() for part in allowed_gates if str(part).strip())
    else:
        allowed_gate_values = AutoOpenConfig.near_miss_fallback_allowed_gates
    allowed_severities = section.get("near_miss_fallback_allowed_severities")
    if isinstance(allowed_severities, str):
        allowed_severity_values = tuple(part.strip().lower() for part in allowed_severities.split(",") if part.strip())
    elif isinstance(allowed_severities, list):
        allowed_severity_values = tuple(str(part).strip().lower() for part in allowed_severities if str(part).strip())
    else:
        allowed_severity_values = AutoOpenConfig.near_miss_fallback_allowed_severities
    hard_fail_gates = section.get("near_miss_fallback_block_hard_fail_gates")
    if isinstance(hard_fail_gates, str):
        hard_fail_gate_values = tuple(part.strip() for part in hard_fail_gates.split(",") if part.strip())
    elif isinstance(hard_fail_gates, list):
        hard_fail_gate_values = tuple(str(part).strip() for part in hard_fail_gates if str(part).strip())
    else:
        hard_fail_gate_values = AutoOpenConfig.near_miss_fallback_block_hard_fail_gates
    allowed_confirmations = section.get("per_symbol_forecast_fallback_allowed_confirmations")
    if isinstance(allowed_confirmations, str):
        allowed_confirmation_values = tuple(part.strip().lower() for part in allowed_confirmations.split(",") if part.strip())
    elif isinstance(allowed_confirmations, list):
        allowed_confirmation_values = tuple(str(part).strip().lower() for part in allowed_confirmations if str(part).strip())
    else:
        allowed_confirmation_values = AutoOpenConfig.per_symbol_forecast_fallback_allowed_confirmations
    return AutoOpenConfig(
        open_enabled=bool(section.get("open_enabled", False)),
        rotate_enabled=bool(section.get("rotate_enabled", False)),
        max_auto_opens_per_day=int(section.get("max_auto_opens_per_day", 3)),
        max_positions=int(section.get("max_positions", AutoOpenConfig.max_positions)),
        min_account_equity_usd=float(section.get("min_account_equity_usd", 250)),
        min_position_value_usd=float(section.get("min_position_value_usd", 50)),
        max_single_position_pct_of_equity=float(section.get("max_single_position_pct_of_equity", 0.20)),
        default_position_pct_of_equity=float(section.get("default_position_pct_of_equity", 0.10)),
        default_position_value_cap_usd=float(section.get("default_position_value_cap_usd", 2500)),
        flat_account_fallback_enabled=bool(section.get("flat_account_fallback_enabled", True)),
        flat_account_fallback_min_score=float(section.get("flat_account_fallback_min_score", 0.40)),
        flat_account_fallback_max_per_day=int(section.get("flat_account_fallback_max_per_day", 1)),
        flat_account_fallback_size_multiplier=float(section.get("flat_account_fallback_size_multiplier", 0.50)),
        near_miss_fallback_enabled=bool(section.get("near_miss_fallback_enabled", True)),
        near_miss_fallback_requires_flat_account=bool(section.get("near_miss_fallback_requires_flat_account", False)),
        near_miss_fallback_max_per_day=int(section.get("near_miss_fallback_max_per_day", 5)),
        near_miss_fallback_size_multiplier=float(section.get("near_miss_fallback_size_multiplier", 0.75)),
        near_miss_fallback_max_distance_pct=float(section.get("near_miss_fallback_max_distance_pct", 0.25)),
        near_miss_fallback_max_file_age_minutes=int(section.get("near_miss_fallback_max_file_age_minutes", 30)),
        near_miss_fallback_allowed_severities=allowed_severity_values,
        near_miss_fallback_allowed_gates=allowed_gate_values,
        near_miss_fallback_block_hard_fail_gates=hard_fail_gate_values,
        per_symbol_forecast_fallback_enabled=bool(section.get("per_symbol_forecast_fallback_enabled", True)),
        per_symbol_forecast_fallback_max_per_day=int(section.get("per_symbol_forecast_fallback_max_per_day", 5)),
        per_symbol_forecast_fallback_size_multiplier=float(section.get("per_symbol_forecast_fallback_size_multiplier", 0.75)),
        per_symbol_forecast_fallback_max_file_age_minutes=int(section.get("per_symbol_forecast_fallback_max_file_age_minutes", 30)),
        per_symbol_forecast_fallback_min_confirmation_score=float(section.get("per_symbol_forecast_fallback_min_confirmation_score", 80)),
        per_symbol_forecast_fallback_min_profitability_score=float(section.get("per_symbol_forecast_fallback_min_profitability_score", 0)),
        per_symbol_forecast_fallback_max_profitability_score=float(section.get("per_symbol_forecast_fallback_max_profitability_score", 300)),
        per_symbol_forecast_fallback_allowed_confirmations=allowed_confirmation_values,
        plan_fallback_enabled=bool(section.get("plan_fallback_enabled", True)),
        plan_fallback_size_multiplier=float(section.get("plan_fallback_size_multiplier", 1.0)),
        plan_fallback_max_file_age_minutes=int(section.get("plan_fallback_max_file_age_minutes", 720)),
        same_day_momentum_enabled=bool(section.get("same_day_momentum_enabled", True)),
        same_day_momentum_max_per_day=int(section.get("same_day_momentum_max_per_day", 8)),
        same_day_momentum_size_multiplier=float(section.get("same_day_momentum_size_multiplier", 0.50)),
        same_day_momentum_min_score=float(section.get("same_day_momentum_min_score", 0.55)),
        same_day_momentum_max_spread_bps=float(section.get("same_day_momentum_max_spread_bps", 35)),
        same_day_momentum_min_dollar_volume=float(section.get("same_day_momentum_min_dollar_volume", 500_000)),
        holding_review_gate_enabled=bool(section.get("holding_review_gate_enabled", True)),
        holding_review_allow_watch=bool(section.get("holding_review_allow_watch", True)),
        max_position_loss_pct=float(section.get("max_position_loss_pct", 2.0)),
        signal_flip_confirmation_clocks=int(section.get("signal_flip_confirmation_clocks", 2)),
        stale_unknown_loss_close_pct=float(section.get("stale_unknown_loss_close_pct", 2.0)),
        trailing_profit_arm_pct=float(section.get("trailing_profit_arm_pct", 2.0)),
        trailing_profit_giveback_pct=float(section.get("trailing_profit_giveback_pct", 1.0)),
        basket_drawdown_pause_pct=float(section.get("basket_drawdown_pause_pct", 1.5)),
        max_long_positions=int(section.get("max_long_positions", 15)),
        max_short_positions=int(section.get("max_short_positions", 15)),
        near_miss_entries_with_open_positions=bool(section.get("near_miss_entries_with_open_positions", True)),
        close_automation_mode=str(section.get("close_automation_mode", "automatic") or "automatic"),
        validation_mode=bool(section.get("validation_mode", True)),
        validation_max_new_orders_per_cycle=int(section.get("validation_max_new_orders_per_cycle", 1)),
        validation_max_new_orders_per_day=int(section.get("validation_max_new_orders_per_day", 2)),
        validation_max_open_positions_total=int(section.get("validation_max_open_positions_total", 3)),
    )


def set_auto_open_enabled(enabled: bool, *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    config_path = Path(path) if path is not None else auto_open_config_path(root)
    payload = _default_payload()
    stored = _read_payload(config_path)
    if stored:
        payload.update(stored)
        section = stored.get("autopilot")
        if isinstance(section, dict):
            payload["autopilot"].update(section)
    payload["autopilot"]["open_enabled"] = bool(enabled)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_auto_open_config(config_path)


def _write_auto_open_config_values(values: dict[str, Any], *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    config_path = Path(path) if path is not None else auto_open_config_path(root)
    payload = _default_payload()
    stored = _read_payload(config_path)
    if stored:
        payload.update(stored)
        section = stored.get("autopilot")
        if isinstance(section, dict):
            payload["autopilot"].update(section)
    payload["autopilot"].update(values)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_auto_open_config(config_path)


def _clean_portal_limit(value: int) -> int:
    return max(0, min(int(value), MAX_PORTAL_LIMIT))


def set_auto_open_max_per_day(max_per_day: int, *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    return _write_auto_open_config_values({"max_auto_opens_per_day": _clean_portal_limit(max_per_day)}, root=root, path=path)


def set_per_symbol_forecast_fallback_max_per_day(max_per_day: int, *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    return _write_auto_open_config_values({"per_symbol_forecast_fallback_max_per_day": _clean_portal_limit(max_per_day)}, root=root, path=path)


def set_auto_open_limit_values(limits: dict[str, int], *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    allowed = {
        "max_auto_opens_per_day",
        "max_positions",
        "flat_account_fallback_max_per_day",
        "near_miss_fallback_max_per_day",
        "per_symbol_forecast_fallback_max_per_day",
        "validation_max_new_orders_per_cycle",
        "validation_max_new_orders_per_day",
        "validation_max_open_positions_total",
    }
    clean = {key: _clean_portal_limit(value) for key, value in limits.items() if key in allowed}
    return _write_auto_open_config_values(clean, root=root, path=path)


def _clean_percent(value: Any, *, minimum: float = 0.0, maximum: float = 100.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = minimum
    return round(max(minimum, min(parsed, maximum)), 4)


def _clean_strategy_mode(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in {"automatic", "mixed", "review_only"} else "mixed"


def _clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def set_auto_open_strategy_values(values: dict[str, Any], *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    clean = {
        "max_position_loss_pct": _clean_percent(values.get("max_position_loss_pct"), maximum=25.0),
        "signal_flip_confirmation_clocks": _clean_portal_limit(values.get("signal_flip_confirmation_clocks", 2)),
        "stale_unknown_loss_close_pct": _clean_percent(values.get("stale_unknown_loss_close_pct"), maximum=25.0),
        "trailing_profit_arm_pct": _clean_percent(values.get("trailing_profit_arm_pct"), maximum=50.0),
        "trailing_profit_giveback_pct": _clean_percent(values.get("trailing_profit_giveback_pct"), maximum=50.0),
        "basket_drawdown_pause_pct": _clean_percent(values.get("basket_drawdown_pause_pct"), maximum=50.0),
        "max_long_positions": _clean_portal_limit(values.get("max_long_positions", 0)),
        "max_short_positions": _clean_portal_limit(values.get("max_short_positions", 0)),
        "near_miss_entries_with_open_positions": _clean_bool(values.get("near_miss_entries_with_open_positions")),
        "close_automation_mode": _clean_strategy_mode(values.get("close_automation_mode")),
    }
    return _write_auto_open_config_values(clean, root=root, path=path)


def position_size_usd(account_equity: float, config: AutoOpenConfig) -> float:
    if account_equity < config.min_account_equity_usd:
        return 0.0
    default_size = min(account_equity * config.default_position_pct_of_equity, config.default_position_value_cap_usd)
    max_size = account_equity * config.max_single_position_pct_of_equity
    size = min(default_size, max_size)
    return round(size, 2) if size >= config.min_position_value_usd else 0.0


def _clean_evidence_value(value: Any) -> Any:
    if value in [None, ""]:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _candidate_pool_model_evidence(path: Path | None = None) -> dict[str, dict[str, Any]]:
    source = path or latest_file(PORTAL_OUTPUTS_DIR, "08_alpaca_paper_candidate_pool_*.csv")
    if source is None or not source.exists():
        return {}
    try:
        frame = pd.read_csv(source, usecols=lambda column: column in {"symbol", "ticker", *MODEL_EVIDENCE_FIELDS}, low_memory=False)
    except Exception:
        return {}
    symbol_col = "symbol" if "symbol" in frame.columns else ("ticker" if "ticker" in frame.columns else "")
    if not symbol_col:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        symbol = str(row.get(symbol_col) or "").strip().upper()
        if not symbol:
            continue
        evidence = {
            key: clean
            for key in MODEL_EVIDENCE_FIELDS
            if key in row
            for clean in [_clean_evidence_value(row.get(key))]
            if clean is not None
        }
        if evidence:
            out[symbol] = evidence
    return out


def _enrich_candidate_with_model_evidence(candidate: dict[str, Any], evidence_by_symbol: dict[str, dict[str, Any]]) -> dict[str, Any]:
    symbol = str(candidate.get("symbol") or "").strip().upper()
    evidence = evidence_by_symbol.get(symbol)
    if not evidence:
        return candidate
    enriched = dict(candidate)
    for key, value in evidence.items():
        enriched.setdefault(key, value)
    details = dict(enriched.get("details") or {})
    details.setdefault("model_evidence_source", "latest_candidate_pool")
    enriched["details"] = details
    return enriched


def latest_strong_candidates(*, engine: Engine | None = None, limit: int = 20) -> list[dict[str, Any]]:
    db = engine or get_engine(required=True)
    with db.connect() as conn:
        latest_tick = conn.execute(select(func.max(intraday_candidate_snapshots.c.snapshot_at))).scalar()
        if latest_tick is None:
            return []
        joined = intraday_promotion_log.join(
            intraday_candidate_snapshots,
            intraday_promotion_log.c.snapshot_id == intraday_candidate_snapshots.c.id,
        )
        rows = conn.execute(
            select(
                intraday_promotion_log.c.symbol,
                intraday_promotion_log.c.promotion_score,
                intraday_candidate_snapshots.c.nightly_bias,
                intraday_candidate_snapshots.c.is_held,
                intraday_candidate_snapshots.c.last_price.label("current_price"),
                intraday_candidate_snapshots.c.details,
            )
            .select_from(joined)
            .where(intraday_candidate_snapshots.c.snapshot_at == latest_tick)
            .where(intraday_promotion_log.c.verdict == "promote_to_selection_strong")
            .order_by(intraday_promotion_log.c.promotion_score.desc(), intraday_promotion_log.c.symbol.asc())
            .limit(limit)
        ).mappings().all()
    evidence_by_symbol = _candidate_pool_model_evidence()
    return [_enrich_candidate_with_model_evidence(dict(row), evidence_by_symbol) for row in rows]



def latest_flat_account_fallback_candidates(*, engine: Engine | None = None, config: AutoOpenConfig | None = None, root: Path | str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    cfg = config or load_auto_open_config(root=root)
    if not cfg.flat_account_fallback_enabled:
        return []
    db = engine or get_engine(required=True)
    full_long_confirmation = {
        "long_trend_5m_positive",
        "long_trend_15m_positive",
        "long_above_vwap_floor",
        "long_range_position_confirmed",
        "long_market_aligned",
    }
    full_short_confirmation = {
        "short_trend_5m_negative",
        "short_trend_15m_negative",
        "short_below_vwap_ceiling",
        "short_range_position_confirmed",
        "short_market_aligned",
    }
    with db.connect() as conn:
        latest_tick = conn.execute(select(func.max(intraday_candidate_snapshots.c.snapshot_at))).scalar()
        if latest_tick is None:
            return []
        joined = intraday_promotion_log.join(
            intraday_candidate_snapshots,
            intraday_promotion_log.c.snapshot_id == intraday_candidate_snapshots.c.id,
        )
        rows = conn.execute(
            select(
                intraday_promotion_log.c.symbol,
                intraday_promotion_log.c.promotion_score,
                intraday_promotion_log.c.contributing,
                intraday_candidate_snapshots.c.nightly_bias,
                intraday_candidate_snapshots.c.is_held,
                intraday_candidate_snapshots.c.spread_bps,
                intraday_candidate_snapshots.c.dollar_volume_today,
                intraday_candidate_snapshots.c.details,
            )
            .select_from(joined)
            .where(intraday_candidate_snapshots.c.snapshot_at == latest_tick)
            .where(intraday_promotion_log.c.verdict == "watch")
            .where(intraday_promotion_log.c.block_reason.is_(None))
            .where(intraday_promotion_log.c.promotion_score >= cfg.flat_account_fallback_min_score)
            .where(intraday_candidate_snapshots.c.spread_bps <= 25)
            .where(intraday_candidate_snapshots.c.dollar_volume_today >= 500_000)
            .order_by(intraday_promotion_log.c.promotion_score.desc(), intraday_promotion_log.c.symbol.asc())
            .limit(limit * 3)
        ).mappings().all()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        contributing = set(payload.get("contributing") or [])
        bias = str(payload.get("nightly_bias") or "").lower()
        required = full_short_confirmation if bias == "short" else full_long_confirmation
        if not required.issubset(contributing):
            continue
        details = dict(payload.get("details") or {})
        details.update({"flat_account_fallback": True, "fallback_reason": "flat_account_no_strong_promotions"})
        payload["details"] = details
        candidates.append(payload)
        if len(candidates) >= limit:
            break
    return candidates


def _near_miss_dir(root: Path | str | None = None) -> Path:
    if root is None:
        return TRADING_DIR / "near_miss"
    return Path(root) / "data" / "trading" / "near_miss"


def _latest_near_miss_file(root: Path | str | None = None) -> Path | None:
    directory = _near_miss_dir(root)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("near_miss_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _per_symbol_forecast_dir(root: Path | str | None = None) -> Path:
    if root is None:
        return TRADING_DIR / "per_symbol_forecast"
    return Path(root) / "data" / "trading" / "per_symbol_forecast"


def _portal_outputs_dir(root: Path | str | None = None) -> Path:
    if root is None:
        return PROJECT_ROOT / "data" / "portal_outputs"
    return Path(root) / "data" / "portal_outputs"


def _latest_per_symbol_forecast_file(root: Path | str | None = None) -> Path | None:
    directory = _per_symbol_forecast_dir(root)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("per_symbol_forecast_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _latest_order_plan_file(root: Path | str | None = None) -> Path | None:
    directory = _portal_outputs_dir(root)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("08_alpaca_paper_order_plan_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def latest_near_miss_fallback_candidates(
    *,
    root: Path | str | None = None,
    config: AutoOpenConfig | None = None,
    limit: int = 5,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_auto_open_config(root=root)
    if not cfg.near_miss_fallback_enabled:
        return []
    path = _latest_near_miss_file(root)
    if path is None:
        return []
    stamp = _aware(now)
    try:
        file_stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return []
    max_age_seconds = max(0, cfg.near_miss_fallback_max_file_age_minutes) * 60
    if max_age_seconds and (stamp - file_stamp).total_seconds() > max_age_seconds:
        return []
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return []
    if frame.empty:
        return []
    allowed_gates = {str(gate).strip() for gate in cfg.near_miss_fallback_allowed_gates if str(gate).strip()}
    allowed_severities = {str(severity).strip().lower() for severity in cfg.near_miss_fallback_allowed_severities if str(severity).strip()}
    hard_fail_gates = {str(gate).strip() for gate in cfg.near_miss_fallback_block_hard_fail_gates if str(gate).strip()}
    frame = frame.copy()
    frame["__severity"] = frame.get("severity", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__status"] = frame.get("status", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__gate"] = frame.get("failed_gate", pd.Series("", index=frame.index)).fillna("").astype(str)
    frame["__distance_pct"] = pd.to_numeric(frame.get("distance_pct", pd.Series(index=frame.index)), errors="coerce")
    frame["__symbol"] = frame.get("symbol", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()
    hard_fail_symbols = set(
        frame[
            frame["__symbol"].ne("")
            & frame["__severity"].eq("hard_fail")
            & frame["__gate"].isin(hard_fail_gates)
        ]["__symbol"].tolist()
    )
    eligible = (
        frame["__symbol"].ne("")
        & frame["__severity"].isin(allowed_severities)
        & frame["__status"].isin(["rejected", "trimmed", "block"])
        & frame["__gate"].isin(allowed_gates)
        & frame["__distance_pct"].notna()
        & (frame["__distance_pct"] <= cfg.near_miss_fallback_max_distance_pct)
        & ~frame["__symbol"].isin(hard_fail_symbols)
    )
    selected = frame[eligible].sort_values(["__distance_pct", "__severity", "__gate", "__symbol"]).drop_duplicates("__symbol").head(limit)
    candidates: list[dict[str, Any]] = []
    for row in selected.fillna("").to_dict("records"):
        side_text = str(row.get("side") or row.get("trade_action") or "").lower()
        bias = "short" if side_text in {"sell", "short"} or "short" in side_text else "long"
        details = {
            "near_miss_fallback": True,
            "fallback_reason": "near_miss_diagnostic_candidate",
            "failed_gate": row.get("failed_gate"),
            "failed_gate_label": row.get("failed_gate_label"),
            "distance_pct": _float(row.get("distance_pct")),
            "distance_to_pass": _float(row.get("distance_to_pass")),
            "severity": row.get("severity"),
            "reason": row.get("reason"),
            "current_price": _float(row.get("current_price") or row.get("last_price") or row.get("close")),
            "is_first_15_min": False,
            "is_last_30_min": False,
        }
        candidates.append(
            {
                "symbol": row.get("__symbol"),
                "promotion_score": _float(row.get("risk_adjusted_score") or row.get("actual_value")),
                "nightly_bias": bias,
                "is_held": False,
                "details": details,
            }
        )
    return candidates


def latest_plan_fallback_candidates(
    *,
    root: Path | str | None = None,
    config: AutoOpenConfig | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_auto_open_config(root=root)
    if not cfg.plan_fallback_enabled:
        return []
    path = _latest_order_plan_file(root)
    if path is None:
        return []
    stamp = _aware(now)
    try:
        file_stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return []
    max_age_seconds = max(0, cfg.plan_fallback_max_file_age_minutes) * 60
    if max_age_seconds and (stamp - file_stamp).total_seconds() > max_age_seconds:
        return []
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return []
    if frame.empty or "symbol" not in frame.columns:
        return []
    out = frame.copy()
    out["__symbol"] = out["symbol"].fillna("").astype(str).str.upper().str.strip()
    out["__status"] = out.get("trade_quality_status", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    out["__qty"] = pd.to_numeric(out.get("suggested_quantity", pd.Series(index=out.index)), errors="coerce").fillna(0)
    out["__notional"] = pd.to_numeric(
        out.get("approved_notional", out.get("notional", pd.Series(index=out.index))),
        errors="coerce",
    ).fillna(0)
    if "notional" in out.columns:
        out["__notional"] = out["__notional"].where(out["__notional"].gt(0), pd.to_numeric(out["notional"], errors="coerce").fillna(0))
    out["__score"] = pd.to_numeric(out.get("risk_adjusted_score", pd.Series(index=out.index)), errors="coerce").fillna(float("-inf"))
    eligible = (
        out["__symbol"].ne("")
        & out["__status"].isin(["approved", "reduced"])
        & out["__qty"].gt(0)
        & out["__notional"].gt(0)
    )
    selected = out[eligible].sort_values(["__status", "__score", "__symbol"], ascending=[True, False, True]).drop_duplicates("__symbol").head(limit)
    candidates: list[dict[str, Any]] = []
    for row in selected.fillna("").to_dict("records"):
        action = str(row.get("trade_action") or row.get("side") or "").strip().lower()
        side = str(row.get("side") or "").strip().lower()
        bias = "short" if action in {"short", "sell"} or side in {"short", "sell"} else "long"
        trade_action = "Short" if bias == "short" else "Long"
        details = {
            "plan_fallback": True,
            "fallback_reason": "latest_approved_order_plan",
            "current_trade_action": trade_action,
            "trading_stream": row.get("trading_stream"),
            "side": "sell" if bias == "short" else "buy",
            "nightly_bias": bias,
            "trade_quality_status": row.get("trade_quality_status"),
            "trade_quality_reason": row.get("trade_quality_reason"),
            "current_price": _float(row.get("current_price")),
            "approved_notional": _float(row.get("approved_notional") or row.get("notional")),
            "suggested_quantity": _float(row.get("suggested_quantity")),
            "is_first_15_min": False,
            "is_last_30_min": False,
        }
        candidates.append(
            {
                "symbol": row.get("__symbol"),
                "promotion_score": _float(row.get("risk_adjusted_score")),
                "nightly_bias": bias,
                "is_held": False,
                "trade_action": trade_action,
                "trade_quality_status": row.get("trade_quality_status"),
                "current_price": row.get("current_price"),
                "details": details,
            }
        )
    return candidates


def latest_per_symbol_forecast_fallback_candidates(
    *,
    root: Path | str | None = None,
    config: AutoOpenConfig | None = None,
    limit: int = 5,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_auto_open_config(root=root)
    if not cfg.per_symbol_forecast_fallback_enabled:
        return []
    path = _latest_per_symbol_forecast_file(root)
    if path is None:
        return []
    stamp = _aware(now)
    try:
        file_stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return []
    max_age_seconds = max(0, cfg.per_symbol_forecast_fallback_max_file_age_minutes) * 60
    if max_age_seconds and (stamp - file_stamp).total_seconds() > max_age_seconds:
        return []
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return []
    if frame.empty:
        return []
    allowed = {
        str(value).strip().lower()
        for value in cfg.per_symbol_forecast_fallback_allowed_confirmations
        if str(value).strip()
    }
    frame = frame.copy()
    frame["__symbol"] = frame.get("symbol", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()
    side_text = frame.get("side", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    action_text = frame.get("current_trade_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__bias"] = "long"
    frame.loc[side_text.isin(["sell", "short"]) | action_text.isin(["short", "sell"]) | side_text.str.contains("short", na=False) | action_text.str.contains("short", na=False), "__bias"] = "short"
    frame["__confirmation"] = frame.get("forecast_confirmation", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__side_alignment"] = frame.get("side_alignment", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__score"] = pd.to_numeric(frame.get("confirmation_score", pd.Series(index=frame.index)), errors="coerce")
    frame["__profitability"] = pd.to_numeric(frame.get("expected_profitability_score", pd.Series(index=frame.index)), errors="coerce")
    frame["__risk_reward"] = frame.get("risk_reward_ok", pd.Series(False, index=frame.index)).fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})
    frame["__profitability_ok"] = frame.get("profitability_ok", pd.Series(False, index=frame.index)).fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})
    frame["__liquidity_ok"] = frame.get("liquidity_ok", pd.Series(False, index=frame.index)).fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})
    frame["__volatility_ok"] = frame.get("volatility_ok", pd.Series(False, index=frame.index)).fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})
    eligible = (
        frame["__symbol"].ne("")
        & frame["__confirmation"].isin(allowed)
        & frame["__side_alignment"].eq("aligned")
        & frame["__score"].ge(cfg.per_symbol_forecast_fallback_min_confirmation_score)
        & frame["__profitability"].ge(cfg.per_symbol_forecast_fallback_min_profitability_score)
        & frame["__profitability"].le(cfg.per_symbol_forecast_fallback_max_profitability_score)
        & frame["__profitability_ok"]
        & frame["__risk_reward"]
        & frame["__liquidity_ok"]
        & frame["__volatility_ok"]
    )
    ranked = frame[eligible].sort_values(["__profitability", "__score", "__symbol"], ascending=[False, False, True]).drop_duplicates("__symbol")
    if limit >= 2 and ranked["__bias"].nunique() > 1:
        short_slots = max(1, limit // 2)
        long_slots = max(1, limit - short_slots)
        selected = pd.concat(
            [
                ranked[ranked["__bias"].eq("long")].head(long_slots),
                ranked[ranked["__bias"].eq("short")].head(short_slots),
            ],
            ignore_index=False,
        ).drop_duplicates("__symbol")
        if len(selected) < limit:
            remaining = ranked[~ranked["__symbol"].isin(selected["__symbol"])].head(limit - len(selected))
            selected = pd.concat([selected, remaining], ignore_index=False).drop_duplicates("__symbol")
        selected = selected.head(limit)
    else:
        selected = ranked.head(limit)
    candidates: list[dict[str, Any]] = []
    for row in selected.fillna("").to_dict("records"):
        bias = str(row.get("__bias") or "long")
        order_side = "sell" if bias == "short" else "buy"
        trade_action = str(row.get("current_trade_action") or ("Short" if bias == "short" else "Long"))
        details = {
            "per_symbol_forecast_fallback": True,
            "fallback_reason": "per_symbol_forecast_confirmed_candidate",
            "current_trade_action": trade_action,
            "side": order_side,
            "nightly_bias": bias,
            "forecast_confirmation": row.get("forecast_confirmation"),
            "confirmation_score": _float(row.get("confirmation_score")),
            "confirmation_reason": row.get("confirmation_reason"),
            "expected_profitability_score": _float(row.get("expected_profitability_score")),
            "expected_move_bps": _float(row.get("expected_move_bps")),
            "current_price": _float(row.get("current_price")),
            "magnitude_bucket": row.get("magnitude_bucket"),
            "direction_context": row.get("direction_context"),
            "side_alignment": row.get("side_alignment"),
            "profitability_ok": bool(row.get("__profitability_ok")),
            "risk_reward_ok": bool(row.get("__risk_reward")),
            "liquidity_ok": bool(row.get("__liquidity_ok")),
            "volatility_ok": bool(row.get("__volatility_ok")),
            "is_first_15_min": False,
            "is_last_30_min": False,
        }
        candidates.append(
            {
                "symbol": row.get("__symbol"),
                "promotion_score": _float(row.get("expected_profitability_score") or row.get("confirmation_score")),
                "nightly_bias": bias,
                "trade_action": trade_action,
                "directional_action": trade_action,
                "directional_strength": min(1.0, max(0.0, _float(row.get("confirmation_score")) / 100.0)),
                "trade_quality_status": "approved",
                "current_trade_action": trade_action,
                "side": order_side,
                "is_held": False,
                "details": details,
            }
        )
    return candidates


def fallback_candidate_strength(candidate: dict[str, Any]) -> float:
    return arbitration_score(candidate)


def ranked_fallback_candidates(
    per_symbol_forecast_candidates: list[dict[str, Any]],
    near_miss_candidates: list[dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return arbitrate_candidates(
        [
            ("per_symbol_forecast", per_symbol_forecast_candidates),
            ("near_miss", near_miss_candidates),
        ],
        limit=limit,
    )


def _todays_open_count(engine: Engine, now: datetime) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count(autopilot_open_log.c.id))
                .where(autopilot_open_log.c.logged_at >= start)
                .where(autopilot_open_log.c.verdict == "opened")
            ).scalar()
            or 0
        )


def _todays_symbol_open_rows(engine: Engine, now: datetime, symbol: str) -> list[dict[str, Any]]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    clean = str(symbol or "").upper()
    with engine.connect() as conn:
        rows = conn.execute(
            select(autopilot_open_log.c.logged_at, autopilot_open_log.c.symbol)
            .where(autopilot_open_log.c.logged_at >= start)
            .where(autopilot_open_log.c.verdict == "opened")
            .where(autopilot_open_log.c.symbol == clean)
        ).mappings().all()
    return [{"symbol": row["symbol"], "opened_at": row["logged_at"], "side": "buy", "action": "open", "source": "autopilot_open_log"} for row in rows]


def _anti_churn_config(root: Path | str | None = None) -> AntiChurnConfig:
    payload = _read_payload(auto_open_config_path(root))
    anti = payload.get("anti_churn") if isinstance(payload.get("anti_churn"), dict) else {}
    limits = payload.get("same_symbol_limits") if isinstance(payload.get("same_symbol_limits"), dict) else {}
    allowed = anti.get("allow_early_close_reasons") if isinstance(anti.get("allow_early_close_reasons"), list) else None
    return AntiChurnConfig(
        enabled=bool(anti.get("enabled", True)),
        minimum_hold_minutes=int(anti.get("minimum_hold_minutes", 30)),
        cooldown_minutes_after_close=int(anti.get("cooldown_minutes_after_close", 60)),
        block_same_cycle_open_close=bool(anti.get("block_same_cycle_open_close", True)),
        block_same_symbol_reopen_same_day=bool(anti.get("block_same_symbol_reopen_same_day", True)),
        block_reverse_same_symbol_same_day=bool(anti.get("block_reverse_same_symbol_same_day", True)),
        max_opens_per_symbol_per_day=int(limits.get("max_opens_per_symbol_per_day", 1)),
        max_closes_per_symbol_per_day=int(limits.get("max_closes_per_symbol_per_day", 1)),
        max_reopens_per_symbol_per_day=int(limits.get("max_reopens_per_symbol_per_day", 0)),
        allow_early_close_reasons=set(allowed) if allowed else AntiChurnConfig().allow_early_close_reasons,
    )


def _todays_detail_open_count(engine: Engine, now: datetime, detail_key: str) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with engine.connect() as conn:
        rows = conn.execute(
            select(autopilot_open_log.c.details)
            .where(autopilot_open_log.c.logged_at >= start)
            .where(autopilot_open_log.c.verdict == "opened")
        ).scalars().all()
    return sum(1 for details in rows if isinstance(details, dict) and details.get(detail_key) is True)


def _todays_fallback_open_count(engine: Engine, now: datetime) -> int:
    return _todays_detail_open_count(engine, now, "flat_account_fallback")


def _todays_near_miss_open_count(engine: Engine, now: datetime) -> int:
    return _todays_detail_open_count(engine, now, "near_miss_fallback")


def _todays_per_symbol_forecast_open_count(engine: Engine, now: datetime) -> int:
    return _todays_detail_open_count(engine, now, "per_symbol_forecast_fallback")


def _todays_same_day_momentum_open_count(engine: Engine, now: datetime) -> int:
    return _todays_detail_open_count(engine, now, "same_day_momentum")


def _candidate_event_type(verdict: str, block_reason: str) -> str:
    reason = str(block_reason or "")
    if verdict == "opened":
        return "candidate_submitted"
    if reason in {"model_meta_label_rejected", "model_evidence_missing"}:
        return "candidate_skipped_meta_label"
    if reason == "asset_not_overnight_tradable":
        return "candidate_skipped_not_overnight_tradable"
    if reason.startswith("anti_churn"):
        return "candidate_skipped_anti_churn"
    return "candidate_blocked"


def _record_candidate_lifecycle_event(
    *,
    symbol: str,
    verdict: str,
    block_reason: str = "",
    order_id: str = "",
    size_usd: float = 0.0,
    details: dict[str, Any] | None = None,
    now: datetime,
) -> None:
    payload = dict(details or {})
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    event_type = _candidate_event_type(verdict, block_reason)
    cycle_id = str(payload.get("cycle_id") or now.strftime("%Y%m%d_%H%M%S"))
    event_session = classify_session_mode(now)
    candidate_source = payload.get("candidate_source") or payload.get("fallback_reason") or payload.get("model_evidence_source") or "paper_autopilot"
    client_order_id = payload.get("client_order_id") or order.get("client_order_id") or ""
    base_lineage = candidate_lineage(
        symbol=symbol,
        cycle_id=cycle_id,
        pipeline_run_id=payload.get("pipeline_run_id", ""),
        candidate_source=candidate_source,
        strategy_mode=payload.get("strategy_mode") or payload.get("strategy_stream") or payload.get("trading_stream") or "paper_autopilot",
        session_mode=payload.get("session_mode") or event_session,
        event_session_mode=event_session,
        planned_execution_session_mode=payload.get("planned_execution_session_mode") or payload.get("session_mode") or event_session,
        actual_submission_session_mode=event_session if event_type == "candidate_submitted" else "",
        model_version=payload.get("model_version", ""),
        side=order.get("side") or payload.get("side"),
        client_order_id=client_order_id,
        candidate_id=payload.get("candidate_id", ""),
        scan_candidate_id=payload.get("scan_candidate_id", ""),
        parent_candidate_id=payload.get("parent_candidate_id", ""),
    )
    lineage_seed = enrich_activity_details({**payload, **base_lineage.values, "symbol": symbol, "client_order_id": client_order_id}, base_lineage)
    if event_type == "candidate_submitted":
        lineage_seed["position_id"] = None
        lineage_seed["trade_id"] = None
        lineage = order_lineage(lineage_seed, broker_order_id=order_id, event_type=event_type)
        details_summary = f"order submitted · {symbol} · {order_id or client_order_id}"
    else:
        lineage = base_lineage
        details_summary = f"{event_type.replace('_', ' ')} · {symbol} · {block_reason or verdict}"
    extended = bool(order.get("extended_hours", payload.get("extended_hours", False)))
    event_key = lineage.values.get("event_key") or f"{cycle_id}:{symbol}:{event_type}:{block_reason}:{order_id}"
    record_event_once(
        position_id_for_symbol(symbol),
        event_type,
        "paper_autopilot",
        enrich_activity_details(
            {
                **payload,
                "event_key": event_key,
                "cycle_id": cycle_id,
                "symbol": symbol,
                "action": "open",
                "candidate_source": candidate_source,
                "session_mode": payload.get("session_mode") or event_session,
                "event_session_mode": event_session,
                "planned_execution_session_mode": payload.get("planned_execution_session_mode") or payload.get("session_mode") or event_session,
                "actual_submission_session_mode": event_session if event_type == "candidate_submitted" else "",
                "extended_hours": extended,
                "overnight_tradable": payload.get("asset_overnight_tradable", payload.get("overnight_tradable", "")),
                "order_type": order.get("type", payload.get("order_type", "")),
                "limit_price": order.get("limit_price", payload.get("limit_price", "")),
                "block_reason": block_reason,
                "verdict": verdict,
                "order_id": order_id,
                "broker_order_id": order_id,
                "client_order_id": client_order_id,
                "size_usd": size_usd,
                "details_summary": details_summary,
                "position_id": None if event_type == "candidate_submitted" else payload.get("position_id"),
                "trade_id": None if event_type == "candidate_submitted" else payload.get("trade_id"),
            },
            lineage,
        ),
        event_key=event_key,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _record_open(
    *,
    symbol: str,
    promotion_score: float | None,
    size_usd: float,
    verdict: str,
    block_reason: str = "",
    order_id: str = "",
    details: dict[str, Any] | None = None,
    engine: Engine,
    now: datetime,
) -> int | None:
    loggable_promotion_score = _float(promotion_score, None)
    if loggable_promotion_score is not None and abs(loggable_promotion_score) >= 10:
        details = {**(details or {}), "promotion_score_unlogged": loggable_promotion_score}
        loggable_promotion_score = None
    safe_details = _json_safe(details or {})
    with engine.begin() as conn:
        result = conn.execute(
            insert(autopilot_open_log).values(
                logged_at=now,
                symbol=symbol,
                promotion_score=loggable_promotion_score,
                size_usd=size_usd,
                verdict=verdict,
                block_reason=block_reason or None,
                order_id=order_id,
                details=safe_details,
            )
        )
        inserted_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
    _record_candidate_lifecycle_event(
        symbol=symbol,
        verdict=verdict,
        block_reason=block_reason or "",
        order_id=order_id or "",
        size_usd=size_usd,
        details=safe_details,
        now=now,
    )
    return inserted_id


def _asset_bool(asset: dict[str, Any] | None, key: str, default: bool = True) -> bool:
    if not isinstance(asset, dict) or key not in asset:
        return default
    value = asset.get(key)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _asset_text(asset: dict[str, Any] | None, key: str) -> str:
    if not isinstance(asset, dict):
        return ""
    return str(asset.get(key) or "").strip()


def _candidate_price(candidate: dict[str, Any], details: dict[str, Any]) -> float:
    for key in ("current_price", "last_price", "close"):
        value = details.get(key, candidate.get(key))
        parsed = _float(value, 0.0)
        if parsed > 0:
            return parsed
    return 0.0


def _whole_share_qty(order_size: float, current_price: float) -> int:
    if order_size <= 0 or current_price <= 0:
        return 0
    return int(math.floor(float(order_size) / float(current_price)))


def _quote_execution_context(symbol: str, side: str, quote_provider: Any | None, stamp: datetime) -> dict[str, Any]:
    if quote_provider is None:
        return {}
    quote = quote_provider.fetch_quote(symbol)
    bid = _float(getattr(quote, "bid", None), 0.0)
    ask = _float(getattr(quote, "ask", None), 0.0)
    last = _float(getattr(quote, "last_price", None), 0.0)
    mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else 0.0
    if str(side).lower() == "buy":
        execution_price = ask or last or mid
    else:
        execution_price = bid or last or mid
    quote_ts = getattr(quote, "quote_ts", None)
    fetched_at = getattr(quote, "fetched_at", None)
    out = {
        "bid": bid or "",
        "ask": ask or "",
        "bid_price": bid or "",
        "ask_price": ask or "",
        "live_bid": bid or "",
        "live_ask": ask or "",
        "live_mid": mid or "",
        "live_last_price": last or "",
        "current_price": execution_price or "",
        "execution_reference_price": execution_price or "",
        "quote_timestamp": quote_ts.isoformat() if quote_ts is not None else "",
        "latest_quote_at": quote_ts.isoformat() if quote_ts is not None else "",
        "quote_fetched_at": fetched_at.isoformat() if fetched_at is not None else stamp.isoformat(),
        "quote_source": getattr(quote, "source", "alpaca"),
    }
    return {key: value for key, value in out.items() if value not in [None, ""]}


def apply_auto_open(
    candidates: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    *,
    mode: str,
    engine: Engine | None = None,
    config: AutoOpenConfig | None = None,
    alpaca_cfg: AlpacaConfig | None = None,
    client: AlpacaPaperClient | None = None,
    quote_provider: Any | None = None,
    now: datetime | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    cfg = config or load_auto_open_config()
    trade_cfg = alpaca_cfg or alpaca_config()
    paper_allow_all = paper_allow_all_override_active(root, config=trade_cfg)
    held = {str(row.get("symbol") or "").upper() for row in open_positions if row.get("symbol")}
    opened = 0
    blocked = 0
    position_intent_rows: list[dict[str, Any]] = []
    notes: list[str] = []
    holding_reviews = latest_holding_review_map(root) if cfg.holding_review_gate_enabled and root is not None else {}

    if mode != "paper_autopilot":
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "mode_not_paper_autopilot"}
    rotation_candidates = any(bool((candidate.get("details") or {}).get("rotation_replacement")) for candidate in candidates)
    if not cfg.open_enabled and not (rotation_candidates and cfg.rotate_enabled):
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "auto_open_disabled"}
    if trade_cfg.live_trading_enabled:
        raise RuntimeError("live trading is disabled for paper autopilot auto-open")
    if not trade_cfg.paper_trading_enabled:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "paper_trading_disabled"}

    kill = kill_switch.gate(action="submit_order", engine=db, now=stamp)
    if not kill.allow:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "kill_switch_active"}

    basket = evaluate_basket_risk(open_positions)
    if basket.new_entries_paused and not rotation_candidates and not paper_allow_all:
        return {
            "autopilot_open_attempted": 0,
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "basket_new_entries_paused",
            "basket_state": basket.basket_state,
            "red_position_pct": basket.red_position_pct,
            "basket_return": basket.basket_return,
            "new_entries_paused": basket.new_entries_paused,
            "basket_risk_reason": basket.reason,
            "basket_risk_reason_text": basket.reason_text,
        }

    equity = _float(getattr(trade_cfg, "account_equity", 0), 0)
    if client is not None:
        try:
            equity = _float(client.get_account().get("equity"), equity)
        except Exception:
            pass
    size = position_size_usd(equity, cfg)
    if size <= 0:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "account_too_small_or_size_below_min"}

    remaining_slots = max(0, cfg.max_positions - len(held))
    daily_remaining = max(0, cfg.max_auto_opens_per_day - _todays_open_count(db, stamp))
    capacity_note = ""
    if remaining_slots <= 0:
        capacity_note = "max_positions_reached"
    elif daily_remaining <= 0:
        capacity_note = "daily_auto_open_cap_reached"
    slots = len(candidates) if rotation_candidates else min(remaining_slots, daily_remaining)
    if cfg.validation_mode:
        validation_remaining_positions = max(0, cfg.validation_max_open_positions_total - len(open_positions))
        validation_daily_remaining = max(0, cfg.validation_max_new_orders_per_day - _todays_open_count(db, stamp))
        validation_cycle_remaining = max(0, cfg.validation_max_new_orders_per_cycle)
        if validation_remaining_positions <= 0:
            capacity_note = "validation_open_position_cap_reached"
        elif validation_daily_remaining <= 0:
            capacity_note = "validation_daily_auto_open_cap_reached"
        elif validation_cycle_remaining <= 0:
            capacity_note = "validation_cycle_auto_open_cap_reached"
        slots = min(slots, validation_cycle_remaining, validation_remaining_positions, validation_daily_remaining)
    if slots <= 0:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": capacity_note or "auto_open_cap_or_basket_full"}

    broker = client or AlpacaPaperClient(trade_cfg)
    default_quote_provider = quote_provider
    if default_quote_provider is None and client is None and bool(trade_cfg.extended_hours or trade_cfg.overnight_trading_enabled):
        default_quote_provider = IntradayProvider(trade_cfg)
    for candidate in candidates:
        if slots <= 0:
            break
        symbol = str(candidate.get("symbol") or "").upper()
        if not symbol or symbol in held or bool(candidate.get("is_held")):
            continue
        details = dict(candidate.get("details") or {})
        is_rotation_replacement = bool(details.get("rotation_replacement"))
        if candidate.get("current_trade_action") and not details.get("current_trade_action"):
            details["current_trade_action"] = candidate.get("current_trade_action")
        if candidate.get("side") and not details.get("side"):
            details["side"] = candidate.get("side")
        if candidate.get("nightly_bias") and not details.get("nightly_bias"):
            details["nightly_bias"] = candidate.get("nightly_bias")
        cycle_id = stamp.strftime("%Y%m%d_%H%M%S")
        for field in LINEAGE_FIELDS:
            if candidate.get(field) not in (None, "") and not details.get(field):
                details[field] = candidate.get(field)
        details.setdefault("cycle_id", cycle_id)
        event_session = classify_session_mode(stamp)
        candidate_source = details.get("candidate_source") or details.get("fallback_reason") or details.get("model_evidence_source") or "paper_autopilot"
        if not details.get("candidate_id"):
            lineage = candidate_lineage(
                symbol=symbol,
                cycle_id=cycle_id,
                pipeline_run_id=details.get("pipeline_run_id", ""),
                candidate_source=candidate_source,
                strategy_mode=details.get("strategy_mode") or details.get("strategy_stream") or details.get("trading_stream") or "paper_autopilot",
                session_mode=details.get("session_mode") or event_session,
                event_session_mode=event_session,
                planned_execution_session_mode=details.get("session_mode") or event_session,
                model_version=details.get("model_version", ""),
                side=details.get("side") or candidate.get("side"),
                client_order_id=details.get("client_order_id", ""),
            )
            details.update({key: value for key, value in lineage.values.items() if value not in (None, "")})
        scan_candidate_id = stable_id("scan", cycle_id, symbol, candidate_source)
        if scan_candidate_id and not details.get("scan_candidate_id"):
            details["scan_candidate_id"] = scan_candidate_id
            if details.get("candidate_id") and details.get("candidate_id") != scan_candidate_id:
                details.setdefault("parent_candidate_id", details.get("candidate_id"))
        details.setdefault("event_session_mode", event_session)
        details.setdefault("planned_execution_session_mode", details.get("session_mode") or event_session)
        scan_event_key = f"{cycle_id}:{symbol}:candidate_scanned"
        scan_payload = {
            **details,
            "event_key": scan_event_key,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "action": "open",
            "candidate_source": candidate_source,
            "session_mode": details.get("session_mode") or event_session,
            "event_session_mode": event_session,
            "extended_hours": bool(trade_cfg.extended_hours or trade_cfg.overnight_trading_enabled),
            "details_summary": f"candidate scanned · {symbol}",
        }
        record_event_once(
            position_id_for_symbol(symbol),
            "candidate_scanned",
            "paper_autopilot",
            scan_payload,
            event_key=scan_event_key,
        )
        if _is_same_day_momentum_candidate(details):
            details.setdefault("strategy_stream", "same_day_momentum")
            details.setdefault("trading_stream", "same_day_momentum")
            details.setdefault("same_day_momentum", True)
            details.setdefault("max_hold_days", 1)
            details.setdefault("must_flatten_eod", True)
            details.setdefault("current_trade_action", details.get("same_day_trade_action") or candidate.get("trade_action") or ("Short" if str(candidate.get("nightly_bias") or "").lower() == "short" else "Long"))
            details.setdefault("side", "sell" if str(details.get("current_trade_action")).lower() == "short" else "buy")
        model_block_reason = model_evidence_block_reason(candidate, details)
        if model_block_reason and not paper_allow_all:
            blocked += 1
            evidence_details = {**details, "model_evidence_status": "blocked", "model_evidence_reason": model_block_reason}
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=0.0, verdict="blocked", block_reason=model_block_reason, details=evidence_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:{model_block_reason}")
            continue
        alignment = evaluate_entry_signal_alignment(candidate, details)
        if not alignment.allowed and not paper_allow_all:
            blocked += 1
            gate_details = {**details, "entry_alignment_status": "blocked", "entry_alignment_reason": alignment.reason}
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=0.0, verdict="blocked", block_reason=alignment.reason, details=gate_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:{alignment.reason}")
            continue
        is_fallback = bool(details.get("flat_account_fallback"))
        is_near_miss = bool(details.get("near_miss_fallback"))
        is_per_symbol_forecast = bool(details.get("per_symbol_forecast_fallback"))
        is_plan_fallback = bool(details.get("plan_fallback"))
        is_same_day_momentum = _is_same_day_momentum_candidate(details)
        if is_per_symbol_forecast and not paper_allow_all:
            order_size = round(size * cfg.per_symbol_forecast_fallback_size_multiplier, 2)
        elif is_same_day_momentum:
            order_size = round(size * cfg.same_day_momentum_size_multiplier, 2)
        elif is_near_miss:
            order_size = round(size * cfg.near_miss_fallback_size_multiplier, 2)
        elif is_plan_fallback:
            order_size = round(size * cfg.plan_fallback_size_multiplier, 2)
        elif is_fallback:
            order_size = round(size * cfg.flat_account_fallback_size_multiplier, 2)
        else:
            order_size = size
        if is_per_symbol_forecast:
            quality_block_reason = per_symbol_forecast_quality_block_reason(details, cfg)
            if quality_block_reason:
                blocked += 1
                quality_details = {**details, "quality_gate_status": "blocked", "quality_block_reason": quality_block_reason}
                _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=quality_block_reason, details=quality_details, engine=db, now=stamp)
                notes.append(f"{symbol}:blocked:{quality_block_reason}")
                continue
        if is_same_day_momentum and not paper_allow_all:
            momentum_block_reason = same_day_momentum_block_reason(candidate, details, cfg)
            if momentum_block_reason:
                blocked += 1
                momentum_details = {**details, "same_day_gate_status": "blocked", "same_day_block_reason": momentum_block_reason}
                _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=momentum_block_reason, details=momentum_details, engine=db, now=stamp)
                notes.append(f"{symbol}:blocked:{momentum_block_reason}")
                continue
        if (is_per_symbol_forecast or is_near_miss or is_fallback) and holding_reviews and not paper_allow_all:
            review = holding_reviews.get(symbol)
            holding_block_reason = holding_review_block_reason(symbol, review, cfg)
            if holding_block_reason:
                blocked += 1
                holding_details = {
                    **details,
                    "holding_review_status": "blocked",
                    "holding_review_reason": holding_block_reason,
                }
                if review:
                    holding_details.update(
                        {
                            "holding_quality": review.get("holding_quality"),
                            "holding_gate_pass": review.get("holding_gate_pass"),
                            "trading_stream": review.get("trading_stream"),
                            "recommended_holding_days": review.get("recommended_holding_days"),
                            "max_holding_days": review.get("max_holding_days"),
                            "holding_gate_reason": review.get("holding_gate_reason"),
                        }
                    )
                _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=holding_block_reason, details=holding_details, engine=db, now=stamp)
                notes.append(f"{symbol}:blocked:{holding_block_reason}")
                continue
        if is_fallback and open_positions and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="fallback_requires_flat_account", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:fallback_requires_flat_account")
            continue
        if is_near_miss and cfg.near_miss_fallback_requires_flat_account and open_positions and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="near_miss_requires_flat_account", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:near_miss_requires_flat_account")
            continue
        if is_fallback and _todays_fallback_open_count(db, stamp) >= cfg.flat_account_fallback_max_per_day and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="fallback_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:fallback_daily_cap_reached")
            continue
        if is_near_miss and _todays_near_miss_open_count(db, stamp) >= cfg.near_miss_fallback_max_per_day and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="near_miss_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:near_miss_daily_cap_reached")
            continue
        if is_per_symbol_forecast and _todays_per_symbol_forecast_open_count(db, stamp) >= cfg.per_symbol_forecast_fallback_max_per_day and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="per_symbol_forecast_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:per_symbol_forecast_daily_cap_reached")
            continue
        if is_same_day_momentum and _todays_same_day_momentum_open_count(db, stamp) >= cfg.same_day_momentum_max_per_day and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="same_day_momentum_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:same_day_momentum_daily_cap_reached")
            continue
        if order_size < cfg.min_position_value_usd:
            blocked += 1
            size_reason = "per_symbol_forecast_size_below_min" if is_per_symbol_forecast else ("near_miss_size_below_min" if is_near_miss else ("plan_fallback_size_below_min" if is_plan_fallback else ("fallback_size_below_min" if is_fallback else "position_size_below_min")))
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=size_reason, details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:position_size_below_min")
            continue
        if (details.get("is_first_15_min") or details.get("is_last_30_min")) and not paper_allow_all:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="near_open_close", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:near_open_close")
            continue
        explicit_side = str(details.get("side") or candidate.get("side") or "").strip().lower()
        explicit_action = str(details.get("current_trade_action") or details.get("trade_action") or candidate.get("current_trade_action") or candidate.get("trade_action") or "").strip().lower()
        if explicit_side in {"sell", "short"} or explicit_action == "short":
            side = "sell"
        elif explicit_side in {"buy", "long"} or explicit_action == "long":
            side = "buy"
        else:
            side = "sell" if str(candidate.get("nightly_bias") or details.get("nightly_bias") or "").lower() == "short" else "buy"
        if side == "sell" and not trade_cfg.allow_short_selling:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="shorting_disabled", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:shorting_disabled")
            continue
        asset: dict[str, Any] | None = None
        if hasattr(broker, "get_asset"):
            try:
                asset = broker.get_asset(symbol)
            except Exception as exc:
                blocked += 1
                asset_details = {**details, "asset_check_error": str(exc)}
                _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="asset_check_failed", details=asset_details, engine=db, now=stamp)
                notes.append(f"{symbol}:blocked:asset_check_failed")
                continue
            if asset is None:
                blocked += 1
                _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="asset_not_found", details=details, engine=db, now=stamp)
                notes.append(f"{symbol}:blocked:asset_not_found")
                continue
        asset_status = _asset_text(asset, "status").lower()
        asset_details = {
            **details,
            "asset_status": asset_status or "",
            "asset_tradable": _asset_bool(asset, "tradable", True),
            "asset_fractionable": _asset_bool(asset, "fractionable", True),
            "asset_shortable": _asset_bool(asset, "shortable", True),
            "asset_overnight_tradable": asset_is_overnight_tradable(asset),
        }
        if not asset_details["asset_tradable"]:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="asset_not_tradable", details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:asset_not_tradable")
            continue
        if asset_status and asset_status != "active":
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=f"asset_status_{asset_status}", details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:asset_status_{asset_status}")
            continue
        if side == "sell" and not asset_details["asset_shortable"]:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="asset_not_shortable", details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:asset_not_shortable")
            continue
        requested_order_type = "limit" if bool(trade_cfg.extended_hours or trade_cfg.overnight_trading_enabled) else "market"
        current_price = _candidate_price(candidate, details)
        quote_data = {
            **candidate,
            **details,
            "current_price": current_price,
            "candidate_reference_price": current_price,
            "side": side,
        }
        if requested_order_type == "limit":
            try:
                live_quote = _quote_execution_context(symbol, side, default_quote_provider, stamp)
            except Exception as exc:
                live_quote = {"quote_fetch_error": str(exc)}
            quote_data.update(live_quote)
            current_price = _float(quote_data.get("execution_reference_price") or quote_data.get("current_price"), current_price)
        policy = session_order_policy(now=stamp, asset=asset, quote=quote_data, requested_order_type=requested_order_type)
        asset_details = {
            **asset_details,
            **{key: quote_data.get(key) for key in ["live_bid", "live_ask", "live_mid", "live_last_price", "execution_reference_price", "quote_timestamp", "latest_quote_at", "quote_fetched_at", "quote_source", "quote_fetch_error"] if quote_data.get(key) not in [None, ""]},
            "session_mode": policy.session_mode,
            "order_policy": policy.order_policy,
            "extended_hours": policy.extended_hours,
            "overnight_tradable": asset_is_overnight_tradable(asset),
            "spread_bps": policy.spread_bps,
            "quote_freshness_seconds": policy.quote_freshness_seconds,
            "quote_executable_price": policy.executable_price,
            "quote_reference_price": policy.reference_price,
            "quote_executable_price_deviation_bps": policy.executable_price_deviation_bps,
            "expected_move_bps": policy.expected_move_bps,
            "estimated_cost_bps": policy.estimated_cost_bps,
            "expected_net_edge_bps": policy.expected_net_edge_bps,
            "edge_to_spread_ratio": policy.edge_to_spread_ratio,
            "spread_gate_decision": policy.spread_gate_decision,
            "session_reject_reason": policy.session_reject_reason,
            "session_max_spread_bps": policy.max_spread_bps,
            "session_size_multiplier": policy.size_multiplier,
        }
        if not policy.allowed and not paper_allow_all:
            blocked += 1
            reason = policy.session_reject_reason or "session_policy_blocked"
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=reason, details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:{reason}")
            continue
        if policy.size_multiplier != 1.0:
            order_size = round(order_size * policy.size_multiplier, 2)
            asset_details["session_adjusted_size_usd"] = order_size
        if order_size < float(cfg.min_position_value_usd):
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="session_size_below_min_position_value", details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:session_size_below_min_position_value")
            continue
        limit_price = extended_limit_price(pd.Series({**candidate, **details, "current_price": current_price}), side, trade_cfg.overnight_limit_buffer_bps) if policy.order_type == "limit" else None
        if policy.order_type == "limit" and limit_price is None:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="session_limit_price_missing", details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:session_limit_price_missing")
            continue
        order = {
            "symbol": symbol,
            "side": side,
            "type": policy.order_type,
            "time_in_force": "day",
            "extended_hours": policy.extended_hours,
            "client_order_id": f"stockml-autopilot-{stamp.strftime('%Y%m%d%H%M%S')}-{symbol}-{side}"[:48],
        }
        if policy.order_type == "limit":
            order["limit_price"] = limit_price
        qty = _whole_share_qty(order_size, current_price)
        if qty < 1 and current_price > 0:
            order_size = round(min(float(trade_cfg.max_notional_per_order), max(order_size, current_price)), 2)
            qty = _whole_share_qty(order_size, current_price)
        asset_details = {**asset_details, "current_price_for_qty": current_price, "computed_qty": qty}
        if qty < 1:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="whole_share_size_below_one", details=asset_details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:whole_share_size_below_one")
            continue
        order["qty"] = str(qty)
        validation = validate_order_payload(order, max_order_notional=trade_cfg.max_notional_per_order)
        if not validation.valid:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=validation.reason, details={**asset_details, "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:{validation.reason}")
            continue
        anti_history = load_recent_trade_history(root=root) + _todays_symbol_open_rows(db, stamp, symbol)
        anti_allowed, anti_report = guard_actions(
            [{"symbol": symbol, "action": "open", "side": side, "reason": "paper_autopilot_open"}],
            open_positions=open_positions,
            trade_history=anti_history,
            now=stamp,
            config=_anti_churn_config(root),
            cycle_id=cycle_id,
        )
        if not anti_allowed:
            blocked += 1
            anti_path = str(write_anti_churn_report(anti_report, root=root, stamp=cycle_id)) if not anti_report.empty else ""
            reason = str(anti_report.iloc[0]["reason"] if not anti_report.empty else "anti_churn_blocked")
            _record_open(
                symbol=symbol,
                promotion_score=candidate.get("promotion_score"),
                size_usd=order_size,
                verdict="blocked",
                block_reason=reason,
                details={**asset_details, "order": order, "anti_churn_report_path": anti_path},
                engine=db,
                now=stamp,
            )
            notes.append(f"{symbol}:blocked:{reason}")
            continue
        if not trade_cfg.submit_orders:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="paper_autopilot_submit_blocked_config", details={**asset_details, "order": order, "final_decision": "BLOCKED_BY_CONFIG"}, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:paper_autopilot_submit_blocked_config")
            continue
        try:
            intent_decision, intent_row = guard_order_submission(
                order,
                client=broker,
                config=PositionIntentConfig(
                    enabled=not paper_allow_all,
                    minimum_hold_minutes=30,
                    allow_short_selling=getattr(trade_cfg, "allow_short_selling", True),
                ),
                now=stamp,
                cycle_id=stamp.strftime("%Y%m%d_%H%M%S"),
                order_source="autopilot_open",
            )
            if not intent_decision.allowed and not paper_allow_all:
                blocked += 1
                position_intent_rows.append(intent_row)
                report_path = write_position_intent_report(position_intent_rows, stamp=stamp.strftime("%Y%m%d_%H%M%S"))
                record_position_intent_block(intent_row, report_path=report_path)
                _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=intent_decision.block_reason, details={**asset_details, "order": order, "position_intent": intent_row}, engine=db, now=stamp)
                notes.append(f"{symbol}:blocked:{intent_decision.block_reason}")
                continue
            paper_only_guard(live_trading_enabled=trade_cfg.live_trading_enabled)
            response = submit_paper_order_payload(order, config=trade_cfg, client=broker)
            order_id = str(response.get("id") or "")
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="opened", order_id=order_id, details={**asset_details, "order": order, "response": response}, engine=db, now=stamp)
            opened += 1
            held.add(symbol)
            slots -= 1
            prefix = "same_day_momentum_opened" if is_same_day_momentum else ("per_symbol_forecast_opened" if is_per_symbol_forecast else ("near_miss_opened" if is_near_miss else ("plan_opened" if is_plan_fallback else ("fallback_opened" if is_fallback else "opened"))))
            notes.append(f"{symbol}:{prefix}:{order_id or 'submitted'}")
        except AlpacaAPIError as exc:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="failed", block_reason="alpaca_api_error", details={**asset_details, **exc.as_dict(), "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:failed:alpaca_api_error")
        except Exception as exc:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="failed", block_reason="submit_exception", details={**asset_details, "error": str(exc), "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:failed:submit_exception")
    return {
        "autopilot_open_attempted": opened + blocked,
        "autopilot_open_submitted": opened,
        "autopilot_open_blocked": blocked,
        "autopilot_open_notes": "; ".join(notes[:10]),
    }
