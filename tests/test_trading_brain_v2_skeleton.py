from pathlib import Path

import pytest

from stockml.trading_brain_v2.autopilot import AUTOPILOT_BLOCKS
from stockml.trading_brain_v2.position_management import POSITION_MANAGEMENT_BLOCKS
from stockml.trading_brain_v2.adapters.execution_handoff import submit_live_order_placeholder
from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.safety import TradingBrainV2LiveExecutionBlocked, assert_v2_live_execution_allowed


def test_v1_remains_active_by_default(tmp_path: Path):
    config_path = tmp_path / "autopilot.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    cfg = load_trading_brain_config(config_path)

    assert cfg.active_version == "v1"
    assert cfg.v1_active is True
    assert cfg.v2_active is False


def test_v2_shadow_mode_can_be_enabled(tmp_path: Path):
    config_path = tmp_path / "autopilot.yaml"
    config_path.write_text(
        "trading_brain:\n"
        "  active_version: v1\n"
        "  v2_shadow_mode: true\n"
        "  v2_allow_live_execution: false\n",
        encoding="utf-8",
    )

    cfg = load_trading_brain_config(config_path)

    assert cfg.v2_shadow_mode is True
    assert cfg.v2_allow_live_execution is False


def test_v2_live_execution_is_disabled_by_default():
    cfg = TradingBrainConfig()

    assert cfg.v2_allow_live_execution is False
    with pytest.raises(TradingBrainV2LiveExecutionBlocked, match="trading_brain_v2_live_execution_disabled"):
        assert_v2_live_execution_allowed(requested_live_execution=True, config=cfg)


def test_v2_live_order_placeholder_is_blocked_when_flag_false():
    cfg = TradingBrainConfig(v2_allow_live_execution=False)

    with pytest.raises(TradingBrainV2LiveExecutionBlocked):
        submit_live_order_placeholder({"symbol": "AAA", "side": "buy"}, config=cfg)


def test_v2_placeholder_blocks_are_registered():
    assert [block.block_id for block in AUTOPILOT_BLOCKS] == [f"AP-B{number:02d}" for number in range(1, 13)]
    assert [block.block_id for block in POSITION_MANAGEMENT_BLOCKS] == [f"PM-B{number:02d}" for number in range(1, 13)]

