from __future__ import annotations

from stockml.trading.config import AlpacaConfig
from stockml.trading.daily_trading_authority import (
    OTHER_BRAIN_BLOCK_REASON,
    load_daily_trading_authority,
    secondary_decision_path_allowed,
)
from stockml.trading.execution_owner import legacy_paper_trader_can_submit


def test_daily_trading_authority_defaults_to_single_paper_autopilot_brain(tmp_path):
    config = tmp_path / "autopilot.yaml"
    config.write_text("version: 1\nexecution_owner: paper_autopilot\n", encoding="utf-8")

    authority = load_daily_trading_authority(config)

    assert authority.single_brain_active is True
    assert authority.decision_owner == "paper_autopilot"
    assert authority.allow_auto_rotations is False
    assert authority.allow_fallback_candidate_brains is False


def test_daily_trading_authority_blocks_secondary_paths_by_default():
    allowed, reason = secondary_decision_path_allowed("auto_rotation")

    assert allowed is False
    assert reason == OTHER_BRAIN_BLOCK_REASON


def test_legacy_paper_trader_is_blocked_by_single_brain_policy():
    cfg = AlpacaConfig(execution_owner="legacy_paper_trader")

    allowed, reason = legacy_paper_trader_can_submit(cfg)

    assert allowed is False
    assert reason == OTHER_BRAIN_BLOCK_REASON
