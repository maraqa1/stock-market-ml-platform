from datetime import datetime, timezone

from stockml.trading import auto_trader
from stockml.trading.auto_trader import _within_auto_trade_window, auto_trading_enabled, run_auto_trader


def test_auto_trading_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STOCKML_ALPACA_AUTOTRADE_ENABLED", raising=False)
    assert auto_trading_enabled() is False


def test_auto_trade_window_respects_weekday_hours(monkeypatch):
    monkeypatch.setenv("STOCKML_ALPACA_AUTOTRADE_START_UTC", "14:45")
    monkeypatch.setenv("STOCKML_ALPACA_AUTOTRADE_END_UTC", "20:30")
    assert _within_auto_trade_window(datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc)) is True
    assert _within_auto_trade_window(datetime(2026, 5, 8, 21, 0, tzinfo=timezone.utc)) is False
    assert _within_auto_trade_window(datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc)) is False


def test_auto_trade_window_can_be_ignored(monkeypatch):
    monkeypatch.setenv("STOCKML_ALPACA_IGNORE_TRADE_WINDOW", "true")
    assert _within_auto_trade_window(datetime(2026, 5, 9, 1, 0, tzinfo=timezone.utc)) is True


def test_auto_trader_skips_cleanly_when_paper_autopilot_blocks_basket(monkeypatch):
    monkeypatch.setenv("STOCKML_ALPACA_AUTOTRADE_ENABLED", "true")
    monkeypatch.setattr(auto_trader, "_within_auto_trade_window", lambda: True)

    def blocked_run(signal_file=None):
        raise RuntimeError("paper_autopilot_running_blocks_basket_submission")

    monkeypatch.setattr(auto_trader, "run_paper_trading", blocked_run)
    monkeypatch.setattr(auto_trader, "refresh_order_tracking", lambda: {"orders_tracked": 3})

    result = run_auto_trader()

    assert result["orders_tracked"] == 3
    assert result["auto_trade_mode"] == "blocked_by_paper_autopilot"
    assert result["block_reason"] == "paper_autopilot_running_blocks_basket_submission"
