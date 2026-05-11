from __future__ import annotations

LIVE_DISABLED_REASON = "live trading is permanently disabled in v1"


class LiveTradingDisabledError(RuntimeError):
    pass


def assert_paper_only(live_trading_enabled: bool = False, mode: str | None = None) -> None:
    if live_trading_enabled or str(mode or "").lower() == "live":
        raise LiveTradingDisabledError(LIVE_DISABLED_REASON)


def paper_only_guard(live_trading_enabled: bool = False, mode: str | None = None) -> bool:
    assert_paper_only(live_trading_enabled=live_trading_enabled, mode=mode)
    return True

