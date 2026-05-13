from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

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
    near_miss_fallback_enabled: bool = False
    near_miss_fallback_requires_flat_account: bool = True
    near_miss_fallback_max_per_day: int = 1
    near_miss_fallback_size_multiplier: float = 0.25
    near_miss_fallback_max_distance_pct: float = 0.10
    near_miss_fallback_allowed_gates: tuple[str, ...] = (
        "risk_adjusted_score_below_threshold",
        "market_cap_below_minimum",
        "volatility_extreme",
    )


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
            "near_miss_fallback_enabled": False,
            "near_miss_fallback_requires_flat_account": True,
            "near_miss_fallback_max_per_day": 1,
            "near_miss_fallback_size_multiplier": 0.25,
            "near_miss_fallback_max_distance_pct": 0.10,
            "near_miss_fallback_allowed_gates": [
                "risk_adjusted_score_below_threshold",
                "market_cap_below_minimum",
                "volatility_extreme",
            ],
        },
    }


def load_auto_open_config(path: Path | str | None = None, *, root: Path | str | None = None) -> AutoOpenConfig:
    config_path = Path(path) if path is not None else auto_open_config_path(root)
    payload: dict[str, Any] = {}
    if config_path.exists():
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    section = payload.get("autopilot") if isinstance(payload, dict) else {}
    section = section if isinstance(section, dict) else {}
    allowed_gates = section.get("near_miss_fallback_allowed_gates")
    if isinstance(allowed_gates, str):
        allowed_gate_values = tuple(part.strip() for part in allowed_gates.split(",") if part.strip())
    elif isinstance(allowed_gates, list):
        allowed_gate_values = tuple(str(part).strip() for part in allowed_gates if str(part).strip())
    else:
        allowed_gate_values = AutoOpenConfig.near_miss_fallback_allowed_gates
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
        near_miss_fallback_enabled=bool(section.get("near_miss_fallback_enabled", False)),
        near_miss_fallback_requires_flat_account=bool(section.get("near_miss_fallback_requires_flat_account", True)),
        near_miss_fallback_max_per_day=int(section.get("near_miss_fallback_max_per_day", 1)),
        near_miss_fallback_size_multiplier=float(section.get("near_miss_fallback_size_multiplier", 0.25)),
        near_miss_fallback_max_distance_pct=float(section.get("near_miss_fallback_max_distance_pct", 0.10)),
        near_miss_fallback_allowed_gates=allowed_gate_values,
    )


def set_auto_open_enabled(enabled: bool, *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    config_path = Path(path) if path is not None else auto_open_config_path(root)
    payload = _default_payload()
    if config_path.exists():
        stored = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(stored, dict):
            payload.update(stored)
            section = stored.get("autopilot")
            if isinstance(section, dict):
                payload["autopilot"].update(section)
    payload["autopilot"]["open_enabled"] = bool(enabled)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_auto_open_config(config_path)


def set_auto_open_max_per_day(max_per_day: int, *, root: Path | str | None = None, path: Path | str | None = None) -> AutoOpenConfig:
    config_path = Path(path) if path is not None else auto_open_config_path(root)
    payload = _default_payload()
    if config_path.exists():
        stored = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(stored, dict):
            payload.update(stored)
            section = stored.get("autopilot")
            if isinstance(section, dict):
                payload["autopilot"].update(section)
    clean = max(0, min(int(max_per_day), 20))
    payload["autopilot"]["max_auto_opens_per_day"] = clean
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_auto_open_config(config_path)


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


def latest_near_miss_fallback_candidates(
    *,
    root: Path | str | None = None,
    config: AutoOpenConfig | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    cfg = config or load_auto_open_config(root=root)
    if not cfg.near_miss_fallback_enabled:
        return []
    path = _latest_near_miss_file(root)
    if path is None:
        return []
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return []
    if frame.empty:
        return []
    allowed_gates = {str(gate).strip() for gate in cfg.near_miss_fallback_allowed_gates if str(gate).strip()}
    frame = frame.copy()
    frame["__severity"] = frame.get("severity", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__status"] = frame.get("status", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame["__gate"] = frame.get("failed_gate", pd.Series("", index=frame.index)).fillna("").astype(str)
    frame["__distance_pct"] = pd.to_numeric(frame.get("distance_pct", pd.Series(index=frame.index)), errors="coerce")
    frame["__symbol"] = frame.get("symbol", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()
    eligible = (
        frame["__symbol"].ne("")
        & frame["__severity"].eq("near_miss")
        & frame["__status"].isin(["rejected", "trimmed", "block"])
        & frame["__gate"].isin(allowed_gates)
        & frame["__distance_pct"].notna()
        & (frame["__distance_pct"] <= cfg.near_miss_fallback_max_distance_pct)
    )
    selected = frame[eligible].sort_values(["__distance_pct", "__gate", "__symbol"]).head(limit)
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
    with engine.begin() as conn:
        result = conn.execute(
            insert(autopilot_open_log).values(
                logged_at=now,
                symbol=symbol,
                promotion_score=promotion_score,
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
        details = candidate.get("details") or {}
        is_fallback = bool(details.get("flat_account_fallback"))
        is_near_miss = bool(details.get("near_miss_fallback"))
        if is_near_miss:
            order_size = round(size * cfg.near_miss_fallback_size_multiplier, 2)
        elif is_fallback:
            order_size = round(size * cfg.flat_account_fallback_size_multiplier, 2)
        else:
            order_size = size
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
        if order_size < cfg.min_position_value_usd:
            blocked += 1
            size_reason = "near_miss_size_below_min" if is_near_miss else ("fallback_size_below_min" if is_fallback else "position_size_below_min")
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
            prefix = "near_miss_opened" if is_near_miss else ("fallback_opened" if is_fallback else "opened")
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
