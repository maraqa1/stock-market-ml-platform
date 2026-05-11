from __future__ import annotations

import os


LIVE_DISABLED_REASON = "live trading is permanently disabled in v1"


def assert_live_disabled() -> None:
    if os.environ.get("ALLOW_LIVE_TRADING") == "1":
        raise RuntimeError(
            "ALLOW_LIVE_TRADING flag is set, but live trading is not implemented "
            "in this codebase. This is a misconfiguration. Refusing to start."
        )
