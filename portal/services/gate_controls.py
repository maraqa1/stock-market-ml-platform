from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from portal.services.latest_file_reader import latest_file, safe_read_csv


@dataclass(frozen=True)
class GateControl:
    id: str
    label: str
    group: str
    enabled: bool
    description: str
    effect_when_on: str
    effect_when_off: str
    source: str
    blocker_keys: tuple[str, ...] = ()
    editable: bool = True


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _nested_get(payload: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _nested_set(payload: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    current = payload
    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[keys[-1]] = value


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_value(root: Path, name: str, default: str = "") -> str:
    for path in (root / ".env", Path("/etc/stockml/stockml.env")):
        try:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return os.environ.get(name, default)


def _set_env_value(root: Path, name: str, value: str) -> None:
    path = root / ".env"
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{name}="):
            out.append(f"{name}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{name}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _latest_blocker_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    frames: list[pd.DataFrame] = []
    for folder, pattern in (
        ("portal_outputs", "08_alpaca_paper_order_results_*.csv"),
        ("portal_outputs", "08_alpaca_paper_order_tracking_*.csv"),
        ("portal_outputs", "08_alpaca_paper_order_plan_*.csv"),
        ("portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"),
    ):
        frame = safe_read_csv(latest_file(root, folder, pattern), nrows=2000)
        if not frame.empty:
            frames.append(frame)
    for frame in frames:
        for column in ("message", "trade_quality_reason", "primary_block_reason", "session_reject_reason", "block_reason", "all_block_reasons"):
            if column not in frame.columns:
                continue
            for value in frame[column].dropna().astype(str):
                for part in value.replace(";", "|").split("|"):
                    key = part.strip().lower().replace(" ", "_")
                    if key and key not in {"nan", "none", "null"}:
                        counts[key] = counts.get(key, 0) + 1
    return counts


def _control_rows(root: Path) -> list[GateControl]:
    autopilot = _read_yaml(root / "config" / "autopilot.yaml")
    trading = _read_yaml(root / "config" / "trading.yaml")
    sessions = _read_yaml(root / "config" / "session_modes.yaml")
    section = autopilot.get("autopilot", {}) if isinstance(autopilot.get("autopilot"), dict) else {}
    anti_churn = autopilot.get("anti_churn", {}) if isinstance(autopilot.get("anti_churn"), dict) else {}
    lifecycle = autopilot.get("position_lifecycle", {}) if isinstance(autopilot.get("position_lifecycle"), dict) else {}
    short_policy = trading.get("short_side_policy", {}) if isinstance(trading.get("short_side_policy"), dict) else {}
    session_modes = sessions.get("session_modes", {}) if isinstance(sessions.get("session_modes"), dict) else {}
    overnight = session_modes.get("overnight_24_5", {}) if isinstance(session_modes.get("overnight_24_5"), dict) else {}
    after_hours = session_modes.get("after_hours", {}) if isinstance(session_modes.get("after_hours"), dict) else {}
    pre_market = session_modes.get("pre_market", {}) if isinstance(session_modes.get("pre_market"), dict) else {}

    short_execution = _bool(short_policy.get("enabled"), False) and _bool(short_policy.get("allow_shorts_in_validation"), False)
    submit_orders = _bool(_env_value(root, "STOCKML_ALPACA_SUBMIT_ORDERS", "false"), False)
    return [
        GateControl("paper_submit", "Paper broker submission", "Execution", submit_orders, "Master switch for submitting paper orders to Alpaca. Live trading remains disabled elsewhere.", "Eligible paper orders may be sent to Alpaca.", "Runs plans and diagnostics only; eligible orders become dry_run/no submission.", ".env", ("plan_only:_no_broker_submission", "paper_autopilot_submit_blocked_config")),
        GateControl("auto_open", "Paper Autopilot auto-open", "Execution", _bool(section.get("open_enabled"), False), "Allows Paper Autopilot to open new paper positions after all gates pass.", "Autopilot may open guarded paper positions.", "Autopilot tracks and closes only; no new paper positions.", "config/autopilot.yaml", ("auto_open_disabled",)),
        GateControl("validation_caps", "Validation caps", "Capacity", _bool(section.get("validation_mode"), True), "Validation-mode caps limit new orders per cycle/day and total validation positions.", "Validation caps can block additional entries.", "Validation caps are ignored; global max order/position controls still apply.", "config/autopilot.yaml", ("validation_daily_auto_open_cap_reached", "validation_cycle_auto_open_cap_reached", "validation_open_position_cap_reached")),
        GateControl("holding_review_gate", "Holding review entry gate", "Model Evidence", _bool(section.get("holding_review_gate_enabled"), True), "Requires the latest holding review to support a new entry when available.", "Weak holding-review evidence can block entries.", "Holding-review evidence is informational only for entries.", "config/autopilot.yaml", ("holding_review_gate_blocked", "holding_edge_not_confirmed")),
        GateControl("per_symbol_forecast_gate", "Per-symbol forecast fallback", "Fallbacks", _bool(section.get("per_symbol_forecast_fallback_enabled"), True), "Allows per-symbol forecast fallback candidates with profitability/liquidity/volatility checks.", "Forecast fallback can add or block candidates using its quality checks.", "Forecast fallback candidates are not used for opens.", "config/autopilot.yaml", ("profitability_not_confirmed", "risk_reward_not_confirmed", "liquidity_not_confirmed", "volatility_not_confirmed", "per_symbol_forecast_daily_cap_reached")),
        GateControl("near_miss_gate", "Near-miss fallback", "Fallbacks", _bool(section.get("near_miss_fallback_enabled"), True), "Allows selected near-miss candidates to be retried when configured.", "Near-miss fallback may add candidates but hard-fail gates still block.", "Near-miss fallback candidates are not used for opens.", "config/autopilot.yaml", ("near_miss_daily_cap_reached", "near_miss_requires_flat_account")),
        GateControl("flat_account_fallback", "Flat-account fallback", "Fallbacks", _bool(section.get("flat_account_fallback_enabled"), True), "Allows fallback candidates when the account is flat.", "Flat-account fallback may open when account has no positions.", "Flat-account fallback is disabled.", "config/autopilot.yaml", ("fallback_daily_cap_reached", "fallback_requires_flat_account")),
        GateControl("same_day_momentum", "Same-day momentum", "Fallbacks", _bool(section.get("same_day_momentum_enabled"), True), "Allows same-day momentum candidates after same-day score, spread, liquidity, and direction checks.", "Same-day momentum can open paper entries.", "Same-day momentum candidates are blocked.", "config/autopilot.yaml", ("same_day_score_below_minimum", "same_day_spread_too_wide", "same_day_liquidity_below_minimum")),
        GateControl("short_execution", "Short execution", "Shorts", short_execution, "Controls whether short candidates can become executable during validation.", "Shorts may execute if every other paper gate passes.", "Short candidates stay research-only / inverse-watch.", "config/trading.yaml", ("short_side_validation_required", "short_side_disabled", "shorting_disabled")),
        GateControl("overnight_24_5_submission", "24/5 overnight submission", "Session", _bool(overnight.get("allow_order_submission"), _bool(overnight.get("enabled"), False)), "Controls whether overnight_24_5 mode can submit paper orders.", "24/5 eligible paper orders may submit.", "24/5 mode evaluates only; no paper orders submit.", "config/session_modes.yaml", ("session_order_submission_disabled", "session_evaluation_only")),
        GateControl("overnight_tradable_gate", "Require overnight-tradable asset", "Session", _bool(overnight.get("require_overnight_tradable"), True), "Requires Alpaca asset metadata to confirm the symbol trades overnight.", "Non-overnight-tradable assets are blocked.", "Overnight-tradable metadata is not used as a blocker.", "config/session_modes.yaml", ("asset_not_overnight_tradable", "asset_not_overnight_tradable")),
        GateControl("after_hours_session", "After-hours session", "Session", _bool(after_hours.get("enabled"), False), "Controls after-hours paper order eligibility.", "After-hours session can submit with session policy.", "After-hours session is evaluation-only/blocked.", "config/session_modes.yaml", ("session_order_submission_disabled",)),
        GateControl("pre_market_session", "Pre-market session", "Session", _bool(pre_market.get("enabled"), False), "Controls pre-market paper order eligibility.", "Pre-market session can submit with session policy.", "Pre-market session is evaluation-only/blocked.", "config/session_modes.yaml", ("session_order_submission_disabled",)),
        GateControl("anti_churn", "Anti-churn guard", "Lifecycle", _bool(anti_churn.get("enabled"), True), "Blocks same-cycle open/close, rapid reopen, and rapid reversal churn.", "Rapid churn is blocked.", "Anti-churn checks are disabled for paper automation.", "config/autopilot.yaml", ("anti_churn", "same_cycle_open_close", "cooldown_after_close", "reverse_same_symbol_same_day")),
        GateControl("position_lifecycle_confirmation", "Exit confirmation guard", "Lifecycle", _bool(lifecycle.get("require_exit_confirmation"), True), "Requires stronger evidence before position lifecycle exits.", "Unconfirmed exit reasons can be blocked.", "Exit confirmation requirement is disabled.", "config/autopilot.yaml", ("latest_signal_unknown", "signal_stale", "minimum_hold_period_not_met")),
    ]


def gate_controls_context(root: Path | str | None = None) -> dict[str, Any]:
    base = Path(root or ".")
    counts = _latest_blocker_counts(base)
    rows = []
    for control in _control_rows(base):
        blockers = sum(count for key, count in counts.items() if any(alias in key for alias in control.blocker_keys))
        rows.append({**control.__dict__, "recent_blocks": blockers})
    top_blockers = [{"reason": key, "count": value} for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]]
    return {"gate_controls": rows, "gate_blockers": top_blockers, "paper_override": paper_override_state(base)}


def paper_override_state(root: Path | str | None = None) -> dict[str, Any]:
    base = Path(root or ".")
    controls = {row.id: row.enabled for row in _control_rows(base)}
    autopilot = _read_yaml(base / "config" / "autopilot.yaml")
    section = autopilot.get("autopilot", {}) if isinstance(autopilot.get("autopilot"), dict) else {}
    active = (
        controls.get("paper_submit", False)
        and controls.get("auto_open", False)
        and not controls.get("validation_caps", True)
        and not controls.get("holding_review_gate", True)
        and controls.get("overnight_24_5_submission", False)
        and not controls.get("overnight_tradable_gate", True)
        and not controls.get("anti_churn", True)
        and not controls.get("position_lifecycle_confirmation", True)
        and int(section.get("max_auto_opens_per_day", 0) or 0) >= 100
        and int(section.get("max_positions", 0) or 0) >= 100
    )
    return {
        "active": active,
        "label": "Active" if active else "Inactive",
        "description": "Paper override removes platform caps/gates for paper execution. Broker, asset, buying-power, and live-disabled safeguards still apply.",
    }


def enable_paper_allow_all_override(*, root: Path | str | None = None) -> dict[str, Any]:
    base = Path(root or ".")
    _set_env_value(base, "STOCKML_ALPACA_SUBMIT_ORDERS", "true")
    _set_env_value(base, "STOCKML_PAPER_TRADING_ENABLED", "true")
    _set_env_value(base, "STOCKML_LIVE_TRADING_ENABLED", "false")
    _set_env_value(base, "STOCKML_ALPACA_MAX_ORDERS", "100")
    _set_env_value(base, "STOCKML_CANDIDATE_POOL_SIZE", "500")

    autopilot_path = base / "config" / "autopilot.yaml"
    autopilot = _read_yaml(autopilot_path)
    for key, value in {
        "open_enabled": True,
        "rotate_enabled": True,
        "max_auto_opens_per_day": 100,
        "max_positions": 100,
        "max_long_positions": 100,
        "max_short_positions": 100,
        "flat_account_fallback_enabled": True,
        "flat_account_fallback_max_per_day": 100,
        "near_miss_fallback_enabled": True,
        "near_miss_fallback_requires_flat_account": False,
        "near_miss_fallback_max_per_day": 100,
        "per_symbol_forecast_fallback_enabled": True,
        "per_symbol_forecast_fallback_max_per_day": 100,
        "plan_fallback_enabled": True,
        "same_day_momentum_enabled": True,
        "same_day_momentum_max_per_day": 100,
        "holding_review_gate_enabled": False,
        "validation_mode": False,
        "validation_max_new_orders_per_cycle": 100,
        "validation_max_new_orders_per_day": 100,
        "validation_max_open_positions_total": 100,
    }.items():
        _nested_set(autopilot, ("autopilot", key), value)
    _nested_set(autopilot, ("anti_churn", "enabled"), False)
    _nested_set(autopilot, ("position_lifecycle", "require_exit_confirmation"), False)
    _nested_set(autopilot, ("position_lifecycle", "stale_signal_is_exit_reason"), True)
    _nested_set(autopilot, ("position_lifecycle", "unknown_signal_is_exit_reason"), True)
    _nested_set(autopilot, ("position_lifecycle", "defensive_close_requires_loss_or_risk_breach"), False)
    _nested_set(autopilot, ("rotation", "rotate_enabled"), True)
    _nested_set(autopilot, ("same_day", "same_day_auto_execution"), True)
    _nested_set(autopilot, ("same_day", "same_day_reversal_close"), True)
    _nested_set(autopilot, ("extended_hours", "enabled"), True)
    _write_yaml(autopilot_path, autopilot)

    trading_path = base / "config" / "trading.yaml"
    trading = _read_yaml(trading_path)
    _nested_set(trading, ("trading", "mode"), "paper")
    _nested_set(trading, ("trading", "dry_run"), False)
    _nested_set(trading, ("trading", "paper_trading_enabled"), True)
    _nested_set(trading, ("trading", "live_trading_enabled"), False)
    _nested_set(trading, ("short_side_policy", "enabled"), True)
    _nested_set(trading, ("short_side_policy", "allow_shorts_in_validation"), True)
    _nested_set(trading, ("short_side_policy", "require_short_side_attribution_pass"), False)
    _nested_set(trading, ("short_side_policy", "research_only_when_disabled"), False)
    _write_yaml(trading_path, trading)

    sessions_path = base / "config" / "session_modes.yaml"
    sessions = _read_yaml(sessions_path)
    for mode in ("regular_session", "pre_market", "after_hours", "overnight_24_5"):
        _nested_set(sessions, ("session_modes", mode, "enabled"), True)
        _nested_set(sessions, ("session_modes", mode, "allow_order_submission"), True)
    _nested_set(sessions, ("session_modes", "pre_market", "position_size_multiplier"), 1.0)
    _nested_set(sessions, ("session_modes", "after_hours", "position_size_multiplier"), 1.0)
    _nested_set(sessions, ("session_modes", "overnight_24_5", "evaluation_only"), False)
    _nested_set(sessions, ("session_modes", "overnight_24_5", "require_overnight_tradable"), False)
    _nested_set(sessions, ("session_modes", "overnight_24_5", "require_not_overnight_halted"), False)
    _nested_set(sessions, ("session_modes", "overnight_24_5", "position_size_multiplier"), 1.0)
    _nested_set(sessions, ("session_modes", "overnight_24_5", "max_new_orders_per_cycle"), 100)
    _nested_set(sessions, ("session_modes", "weekend_closed", "allow_order_submission"), False)
    _write_yaml(sessions_path, sessions)

    return {"status": "ok", "paper_override": paper_override_state(base)}


def set_gate_control(control_id: str, enabled: bool, *, root: Path | str | None = None) -> dict[str, Any]:
    base = Path(root or ".")
    if control_id == "paper_submit":
        _set_env_value(base, "STOCKML_ALPACA_SUBMIT_ORDERS", "true" if enabled else "false")
    elif control_id == "auto_open":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "open_enabled"), enabled)
    elif control_id == "validation_caps":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "validation_mode"), enabled)
    elif control_id == "holding_review_gate":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "holding_review_gate_enabled"), enabled)
    elif control_id == "per_symbol_forecast_gate":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "per_symbol_forecast_fallback_enabled"), enabled)
    elif control_id == "near_miss_gate":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "near_miss_fallback_enabled"), enabled)
    elif control_id == "flat_account_fallback":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "flat_account_fallback_enabled"), enabled)
    elif control_id == "same_day_momentum":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("autopilot", "same_day_momentum_enabled"), enabled)
    elif control_id == "short_execution":
        path = base / "config" / "trading.yaml"
        payload = _read_yaml(path)
        _nested_set(payload, ("short_side_policy", "enabled"), enabled)
        _nested_set(payload, ("short_side_policy", "allow_shorts_in_validation"), enabled)
        _nested_set(payload, ("short_side_policy", "research_only_when_disabled"), not enabled)
        _write_yaml(path, payload)
    elif control_id == "overnight_24_5_submission":
        _set_yaml_value(base / "config" / "session_modes.yaml", ("session_modes", "overnight_24_5", "allow_order_submission"), enabled)
        _set_yaml_value(base / "config" / "session_modes.yaml", ("session_modes", "overnight_24_5", "enabled"), enabled)
    elif control_id == "overnight_tradable_gate":
        _set_yaml_value(base / "config" / "session_modes.yaml", ("session_modes", "overnight_24_5", "require_overnight_tradable"), enabled)
    elif control_id == "after_hours_session":
        _set_yaml_value(base / "config" / "session_modes.yaml", ("session_modes", "after_hours", "enabled"), enabled)
    elif control_id == "pre_market_session":
        _set_yaml_value(base / "config" / "session_modes.yaml", ("session_modes", "pre_market", "enabled"), enabled)
    elif control_id == "anti_churn":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("anti_churn", "enabled"), enabled)
    elif control_id == "position_lifecycle_confirmation":
        _set_yaml_value(base / "config" / "autopilot.yaml", ("position_lifecycle", "require_exit_confirmation"), enabled)
    else:
        raise KeyError(f"unknown_gate_control:{control_id}")
    return {"status": "ok", "control_id": control_id, "enabled": bool(enabled)}


def _set_yaml_value(path: Path, keys: tuple[str, ...], value: Any) -> None:
    payload = _read_yaml(path)
    _nested_set(payload, keys, value)
    _write_yaml(path, payload)
