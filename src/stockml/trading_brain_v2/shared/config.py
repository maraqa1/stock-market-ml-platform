from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "config" / "autopilot.yaml"


@dataclass(frozen=True)
class TradingBrainConfig:
    active_version: str = "v1"
    v2_shadow_mode: bool = True
    v2_allow_live_execution: bool = False
    v2_paper_execution: bool = False
    ai2_enrichment_enabled: bool = True
    ai2_enrichment_provider: str = "ai2"
    ai2_enrichment_input_mode: str = "raw_candidate_pool"
    ai2_enrichment_output_dir: str = "data/ai2"
    ai2_enrichment_fail_safe_on_error: bool = True
    ai2_enrichment_endpoint_url: str = ""
    ai2_enrichment_api_key_env: str = "AI2_API_KEY"
    ai2_enrichment_api_key: str = ""
    ai2_enrichment_timeout_seconds: float = 120.0
    ai2_enrichment_auth_header: str = "Authorization"
    max_live_gap_block_pct: float = 0.05
    max_live_gap_refresh_pct: float = 0.025
    min_price: float = 0.0
    min_volume: float = 0.0
    low_volume_action: str = "ENTER_REDUCED"
    vol_20d_full_size_max: float = 0.03
    vol_20d_75_size_max: float = 0.05
    vol_20d_50_size_max: float = 0.07
    vol_20d_25_size_max: float = 0.09
    vol_20d_block_above: float = 0.09
    five_day_full_size_max: float = 0.10
    five_day_75_size_max: float = 0.15
    five_day_50_size_max: float = 0.25
    five_day_25_size_max: float = 0.30
    five_day_block_above: float = 0.30
    clean_proceed_stop_pct: float = 0.04
    reduced_review_stop_pct: float = 0.03
    high_volatility_stop_pct: float = 0.025
    clean_trailing_stop_pct: float = 0.03
    reduced_trailing_stop_pct: float = 0.02
    high_volatility_trailing_stop_pct: float = 0.018
    take_profit_1_pct: float = 0.02
    take_profit_2_pct: float = 0.04
    trail_after_profit_pct: float = 0.06
    breakeven_move_pct: float = 0.01
    max_open_positions: int = 10
    max_single_name_exposure_pct: float = 0.15
    max_daily_loss_pct: float = 0.02
    max_review_adjusted_exposure_pct: float = 0.30

    @property
    def v1_active(self) -> bool:
        return self.active_version == "v1"

    @property
    def v2_active(self) -> bool:
        return self.active_version == "v2"


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_trading_brain_policy(config: TradingBrainConfig) -> None:
    thresholds = [
        config.max_live_gap_refresh_pct,
        config.max_live_gap_block_pct,
        config.vol_20d_full_size_max,
        config.vol_20d_75_size_max,
        config.vol_20d_50_size_max,
        config.vol_20d_25_size_max,
        config.vol_20d_block_above,
        config.five_day_full_size_max,
        config.five_day_75_size_max,
        config.five_day_50_size_max,
        config.five_day_25_size_max,
        config.five_day_block_above,
    ]
    if any(value < 0 for value in thresholds):
        raise ValueError("trading_brain_v2_policy_negative_threshold")
    if config.max_live_gap_refresh_pct > config.max_live_gap_block_pct:
        raise ValueError("trading_brain_v2_policy_refresh_gap_gt_block_gap")
    if config.v2_allow_live_execution and not config.v2_paper_execution:
        raise ValueError("trading_brain_v2_live_requires_explicit_separate_confirmation")


def assert_startup_safety(config: TradingBrainConfig, *, policy_available: bool = True, audit_available: bool = True) -> None:
    if config.active_version == "v2":
        if not policy_available:
            raise RuntimeError("trading_brain_v2_policy_missing")
        if not audit_available:
            raise RuntimeError("trading_brain_v2_audit_missing")
        if config.v2_allow_live_execution:
            raise RuntimeError("trading_brain_v2_live_execution_not_allowed_for_paper_activation")
        if not config.v2_paper_execution:
            raise RuntimeError("trading_brain_v2_paper_execution_not_enabled")


def load_trading_brain_config(path: Path | str | None = CONFIG_PATH) -> TradingBrainConfig:
    payload: dict[str, Any] = {}
    config_path = Path(path) if path is not None else CONFIG_PATH
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        payload = loaded if isinstance(loaded, dict) else {}
    section = payload.get("trading_brain")
    section = section if isinstance(section, dict) else {}
    legacy_v2_section = payload.get("trading_brain_v2")
    legacy_v2_section = legacy_v2_section if isinstance(legacy_v2_section, dict) else {}
    policy = section.get("v2_policy")
    policy = policy if isinstance(policy, dict) else {}
    ai2_enrichment = section.get("ai2_enrichment") or legacy_v2_section.get("ai2_enrichment")
    ai2_enrichment = ai2_enrichment if isinstance(ai2_enrichment, dict) else {}
    enrichment_provider = str(ai2_enrichment.get("provider") or "ai2").strip().lower() or "ai2"
    provider_env_prefix = enrichment_provider.upper().replace("-", "_")
    endpoint_url = (
        ai2_enrichment.get("endpoint_url")
        or os.environ.get("ENRICHMENT_ENDPOINT", "")
        or os.environ.get(f"{provider_env_prefix}_ENRICHMENT_ENDPOINT", "")
        or os.environ.get("AI2_ENRICHMENT_ENDPOINT", "")
    )
    api_key_env = str(ai2_enrichment.get("api_key_env") or f"{provider_env_prefix}_API_KEY").strip() or f"{provider_env_prefix}_API_KEY"
    cfg = TradingBrainConfig(
        active_version=str(section.get("active_version") or "v1").strip().lower() or "v1",
        v2_shadow_mode=_bool(section.get("v2_shadow_mode"), True),
        v2_allow_live_execution=_bool(section.get("v2_allow_live_execution"), False),
        v2_paper_execution=_bool(section.get("v2_paper_execution"), False),
        ai2_enrichment_enabled=_bool(ai2_enrichment.get("enabled"), True),
        ai2_enrichment_provider=enrichment_provider,
        ai2_enrichment_input_mode=str(ai2_enrichment.get("input_mode") or "raw_candidate_pool").strip() or "raw_candidate_pool",
        ai2_enrichment_output_dir=str(ai2_enrichment.get("output_dir") or "data/ai2").strip() or "data/ai2",
        ai2_enrichment_fail_safe_on_error=_bool(ai2_enrichment.get("fail_safe_on_error"), True),
        ai2_enrichment_endpoint_url=str(endpoint_url or "").strip(),
        ai2_enrichment_api_key_env=api_key_env,
        ai2_enrichment_api_key=str(os.environ.get(api_key_env, "")).strip(),
        ai2_enrichment_timeout_seconds=_float(ai2_enrichment.get("timeout_seconds"), 120.0),
        ai2_enrichment_auth_header=str(ai2_enrichment.get("auth_header") or "Authorization").strip() or "Authorization",
        max_live_gap_block_pct=_float(policy.get("max_live_gap_block_pct"), 0.05),
        max_live_gap_refresh_pct=_float(policy.get("max_live_gap_refresh_pct"), 0.025),
        min_price=_float(policy.get("min_price"), 0.0),
        min_volume=_float(policy.get("min_volume"), 0.0),
        low_volume_action=str(policy.get("low_volume_action") or "ENTER_REDUCED").strip().upper(),
        vol_20d_full_size_max=_float(policy.get("vol_20d_full_size_max"), 0.03),
        vol_20d_75_size_max=_float(policy.get("vol_20d_75_size_max"), 0.05),
        vol_20d_50_size_max=_float(policy.get("vol_20d_50_size_max"), 0.07),
        vol_20d_25_size_max=_float(policy.get("vol_20d_25_size_max"), 0.09),
        vol_20d_block_above=_float(policy.get("vol_20d_block_above"), 0.09),
        five_day_full_size_max=_float(policy.get("five_day_full_size_max"), 0.10),
        five_day_75_size_max=_float(policy.get("five_day_75_size_max"), 0.15),
        five_day_50_size_max=_float(policy.get("five_day_50_size_max"), 0.25),
        five_day_25_size_max=_float(policy.get("five_day_25_size_max"), 0.30),
        five_day_block_above=_float(policy.get("five_day_block_above"), 0.30),
        clean_proceed_stop_pct=_float(policy.get("clean_proceed_stop_pct"), 0.04),
        reduced_review_stop_pct=_float(policy.get("reduced_review_stop_pct"), 0.03),
        high_volatility_stop_pct=_float(policy.get("high_volatility_stop_pct"), 0.025),
        clean_trailing_stop_pct=_float(policy.get("clean_trailing_stop_pct"), 0.03),
        reduced_trailing_stop_pct=_float(policy.get("reduced_trailing_stop_pct"), 0.02),
        high_volatility_trailing_stop_pct=_float(policy.get("high_volatility_trailing_stop_pct"), 0.018),
        take_profit_1_pct=_float(policy.get("take_profit_1_pct"), 0.02),
        take_profit_2_pct=_float(policy.get("take_profit_2_pct"), 0.04),
        trail_after_profit_pct=_float(policy.get("trail_after_profit_pct"), 0.06),
        breakeven_move_pct=_float(policy.get("breakeven_move_pct"), 0.01),
        max_open_positions=_int(policy.get("max_open_positions"), 10),
        max_single_name_exposure_pct=_float(policy.get("max_single_name_exposure_pct"), 0.15),
        max_daily_loss_pct=_float(policy.get("max_daily_loss_pct"), 0.02),
        max_review_adjusted_exposure_pct=_float(policy.get("max_review_adjusted_exposure_pct"), 0.30),
    )
    validate_trading_brain_policy(cfg)
    return cfg
