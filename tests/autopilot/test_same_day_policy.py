from stockml.autopilot.policy import same_day_daily_loss_halt
from stockml.trading.position_sizing import SameDaySizingConfig


def test_same_day_policy_halts_only_same_day_after_loss_limit():
    result = same_day_daily_loss_halt(-50, config=SameDaySizingConfig(max_loss_per_day_usd=-50))

    assert result["halted"] is True
    assert result["reason"] == "REJECTED_SAME_DAY_LOSS_LIMIT"
    assert result["stream"] == "same_day_momentum"
    assert result["multi_day_unaffected"] is True


def test_same_day_policy_allows_before_loss_limit():
    result = same_day_daily_loss_halt(-10, config=SameDaySizingConfig(max_loss_per_day_usd=-50))

    assert result["halted"] is False
    assert result["reason"] == ""
