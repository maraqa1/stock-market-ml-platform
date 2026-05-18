from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from stockml.autopilot.candidate_arbitration import arbitrate_candidates, arbitration_score
from stockml.common.paths import PROJECT_ROOT, TRADING_DIR
from stockml.db.connection import get_engine
from stockml.db.schema import autopilot_open_log, intraday_candidate_snapshots, intraday_promotion_log
from stockml.intraday import kill_switch
from stockml.safety.paper_only_guard import paper_only_guard
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config
from stockml.trading.execution_engine import submit_paper_order_payload
from stockml.trading.order_builder import validate_order_payload


CONFIG_PATH = PROJECT_ROOT / "config" / "autopilot.yaml"
MAX_PORTAL_LIMIT = 100


@dataclass(frozen=True)
class AutoOpenConfig:
    open_enabled: bool = False
    rotate_enabled: bool = False
    max_auto_opens_per_day: int = 3
    max_positions: int = 5
    min_account_equity_usd: float = 250.0
    min_position_value_usd: float = 50.0
    max_single_position_pct_of_equity: float = 0.20
    default_position_pct_of_equity: float = 0.10
    default_position_value_cap_usd: float = 200.0
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
            "max_positions": 5,
            "min_account_equity_usd": 250,
            "min_position_value_usd": 50,
            "max_single_position_pct_of_equity": 0.20,
            "default_position_pct_of_equity": 0.10,
            "default_position_value_cap_usd": 200,
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
        max_positions=int(section.get("max_positions", 5)),
        min_account_equity_usd=float(section.get("min_account_equity_usd", 250)),
        min_position_value_usd=float(section.get("min_position_value_usd", 50)),
        max_single_position_pct_of_equity=float(section.get("max_single_position_pct_of_equity", 0.20)),
        default_position_pct_of_equity=float(section.get("default_position_pct_of_equity", 0.10)),
        default_position_value_cap_usd=float(section.get("default_position_value_cap_usd", 200)),
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
    }
    clean = {key: _clean_portal_limit(value) for key, value in limits.items() if key in allowed}
    return _write_auto_open_config_values(clean, root=root, path=path)


def position_size_usd(account_equity: float, config: AutoOpenConfig) -> float:
    if account_equity < config.min_account_equity_usd:
        return 0.0
    default_size = min(account_equity * config.default_position_pct_of_equity, config.default_position_value_cap_usd)
    max_size = account_equity * config.max_single_position_pct_of_equity
    size = min(default_size, max_size)
    return round(size, 2) if size >= config.min_position_value_usd else 0.0


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
                intraday_candidate_snapshots.c.details,
            )
            .select_from(joined)
            .where(intraday_candidate_snapshots.c.snapshot_at == latest_tick)
            .where(intraday_promotion_log.c.verdict == "promote_to_selection_strong")
            .order_by(intraday_promotion_log.c.promotion_score.desc(), intraday_promotion_log.c.symbol.asc())
            .limit(limit)
        ).mappings().all()
    return [dict(row) for row in rows]


def latest_flat_account_fallback_candidates(*, engine: Engine | None = None, config: AutoOpenConfig | None = None, limit: int = 5) -> list[dict[str, Any]]:
    cfg = config or load_auto_open_config()
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


def _latest_per_symbol_forecast_file(root: Path | str | None = None) -> Path | None:
    directory = _per_symbol_forecast_dir(root)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("per_symbol_forecast_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
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
                details=details or {},
            )
        )
        return result.inserted_primary_key[0] if result.inserted_primary_key else None


def apply_auto_open(
    candidates: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    *,
    mode: str,
    engine: Engine | None = None,
    config: AutoOpenConfig | None = None,
    alpaca_cfg: AlpacaConfig | None = None,
    client: AlpacaPaperClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    db = engine or get_engine(required=True)
    stamp = _aware(now)
    cfg = config or load_auto_open_config()
    trade_cfg = alpaca_cfg or alpaca_config()
    held = {str(row.get("symbol") or "").upper() for row in open_positions if row.get("symbol")}
    opened = 0
    blocked = 0
    notes: list[str] = []

    if mode != "paper_autopilot":
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "mode_not_paper_autopilot"}
    if not cfg.open_enabled:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "auto_open_disabled"}
    if trade_cfg.live_trading_enabled:
        raise RuntimeError("live trading is disabled for paper autopilot auto-open")
    if not trade_cfg.paper_trading_enabled:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "paper_trading_disabled"}

    kill = kill_switch.gate(action="submit_order", engine=db, now=stamp)
    if not kill.allow:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "kill_switch_active"}

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
    slots = min(remaining_slots, daily_remaining)
    if slots <= 0:
        return {"autopilot_open_attempted": 0, "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "auto_open_cap_or_basket_full"}

    broker = client or AlpacaPaperClient(trade_cfg)
    for candidate in candidates:
        if slots <= 0:
            break
        symbol = str(candidate.get("symbol") or "").upper()
        if not symbol or symbol in held or bool(candidate.get("is_held")):
            continue
        details = dict(candidate.get("details") or {})
        if candidate.get("current_trade_action") and not details.get("current_trade_action"):
            details["current_trade_action"] = candidate.get("current_trade_action")
        if candidate.get("side") and not details.get("side"):
            details["side"] = candidate.get("side")
        if candidate.get("nightly_bias") and not details.get("nightly_bias"):
            details["nightly_bias"] = candidate.get("nightly_bias")
        is_fallback = bool(details.get("flat_account_fallback"))
        is_near_miss = bool(details.get("near_miss_fallback"))
        is_per_symbol_forecast = bool(details.get("per_symbol_forecast_fallback"))
        if is_per_symbol_forecast:
            order_size = round(size * cfg.per_symbol_forecast_fallback_size_multiplier, 2)
        elif is_near_miss:
            order_size = round(size * cfg.near_miss_fallback_size_multiplier, 2)
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
        if is_fallback and open_positions:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="fallback_requires_flat_account", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:fallback_requires_flat_account")
            continue
        if is_near_miss and cfg.near_miss_fallback_requires_flat_account and open_positions:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="near_miss_requires_flat_account", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:near_miss_requires_flat_account")
            continue
        if is_fallback and _todays_fallback_open_count(db, stamp) >= cfg.flat_account_fallback_max_per_day:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="fallback_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:fallback_daily_cap_reached")
            continue
        if is_near_miss and _todays_near_miss_open_count(db, stamp) >= cfg.near_miss_fallback_max_per_day:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="near_miss_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:near_miss_daily_cap_reached")
            continue
        if is_per_symbol_forecast and _todays_per_symbol_forecast_open_count(db, stamp) >= cfg.per_symbol_forecast_fallback_max_per_day:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="per_symbol_forecast_daily_cap_reached", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:per_symbol_forecast_daily_cap_reached")
            continue
        if order_size < cfg.min_position_value_usd:
            blocked += 1
            size_reason = "per_symbol_forecast_size_below_min" if is_per_symbol_forecast else ("near_miss_size_below_min" if is_near_miss else ("fallback_size_below_min" if is_fallback else "position_size_below_min"))
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=size_reason, details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:position_size_below_min")
            continue
        if details.get("is_first_15_min") or details.get("is_last_30_min"):
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="near_open_close", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:near_open_close")
            continue
        side = "sell" if str(candidate.get("nightly_bias") or "").lower() == "short" else "buy"
        if side == "sell" and not trade_cfg.allow_short_selling:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="shorting_disabled", details=details, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:shorting_disabled")
            continue
        order = {
            "symbol": symbol,
            "notional": str(round(order_size, 2)),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "extended_hours": False,
            "client_order_id": f"stockml-autopilot-{stamp.strftime('%Y%m%d%H%M%S')}-{symbol}-{side}"[:48],
        }
        validation = validate_order_payload(order, max_order_notional=trade_cfg.max_notional_per_order)
        if not validation.valid:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason=validation.reason, details={**details, "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:{validation.reason}")
            continue
        if not trade_cfg.submit_orders:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="blocked", block_reason="submit_orders_disabled", details={**details, "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:blocked:submit_orders_disabled")
            continue
        try:
            paper_only_guard(live_trading_enabled=trade_cfg.live_trading_enabled)
            response = submit_paper_order_payload(order, config=trade_cfg, client=broker)
            order_id = str(response.get("id") or "")
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="opened", order_id=order_id, details={**details, "order": order, "response": response}, engine=db, now=stamp)
            opened += 1
            held.add(symbol)
            slots -= 1
            prefix = "per_symbol_forecast_opened" if is_per_symbol_forecast else ("near_miss_opened" if is_near_miss else ("fallback_opened" if is_fallback else "opened"))
            notes.append(f"{symbol}:{prefix}:{order_id or 'submitted'}")
        except AlpacaAPIError as exc:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="failed", block_reason="alpaca_api_error", details={**details, **exc.as_dict(), "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:failed:alpaca_api_error")
        except Exception as exc:
            blocked += 1
            _record_open(symbol=symbol, promotion_score=candidate.get("promotion_score"), size_usd=order_size, verdict="failed", block_reason="submit_exception", details={**details, "error": str(exc), "order": order}, engine=db, now=stamp)
            notes.append(f"{symbol}:failed:submit_exception")
    return {
        "autopilot_open_attempted": opened + blocked,
        "autopilot_open_submitted": opened,
        "autopilot_open_blocked": blocked,
        "autopilot_open_notes": "; ".join(notes[:10]),
    }
