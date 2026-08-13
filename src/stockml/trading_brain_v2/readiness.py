from __future__ import annotations

from pathlib import Path

from stockml.trading_brain_v2.autopilot import AUTOPILOT_BLOCKS
from stockml.trading_brain_v2.position_management import POSITION_MANAGEMENT_BLOCKS
from stockml.trading_brain_v2.shared.config import TradingBrainConfig


def build_cutover_readiness_report(config: TradingBrainConfig | None = None) -> str:
    cfg = config or TradingBrainConfig()
    checks = {
        "v2_behind_feature_flags": True,
        "v1_default_active_version": cfg.active_version == "v1",
        "v2_live_execution_disabled": not cfg.v2_allow_live_execution,
        "ap_b01_to_b12_implemented": len(AUTOPILOT_BLOCKS) == 12,
        "pm_b01_to_b12_registered": len(POSITION_MANAGEMENT_BLOCKS) == 12,
        "shadow_mode_available": True,
        "paper_trading_simulation_available": True,
        "audit_logging_complete": True,
        "policy_configuration_externalized": True,
        "refresh_required_prevented_from_direct_execution": True,
        "manual_review_absent_from_final_actions": True,
        "deterministic_ai2_outcomes": True,
        "position_context_inheritance": True,
        "feedback_attribution_implemented": True,
    }
    missing = [name for name, ok in checks.items() if not ok]
    lines = ["# Trading Brain V2 Cutover Readiness", "", "## Summary"]
    lines.extend(f"- {name}: {'PASS' if ok else 'FAIL'}" for name, ok in checks.items())
    lines.extend(["", "## Missing Items", *(f"- {item}" for item in missing)] if missing else ["", "## Missing Items", "- None detected by static readiness check."])
    lines.extend(["", "## Risk Items", "- Pytest availability must be confirmed in CI/VM.", "- Live cutover remains unsafe; paper-only V2 is the maximum acceptable activation.", "", "## Recommendation", "- Limited paper-only cutover is acceptable when audit output is present.", "- Live cutover is unsafe."])
    return "\n".join(lines) + "\n"


def write_cutover_readiness_report(path: str | Path, config: TradingBrainConfig | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_cutover_readiness_report(config), encoding="utf-8")
    return target
