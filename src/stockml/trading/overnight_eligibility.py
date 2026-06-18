from __future__ import annotations

from typing import Any


def _bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def is_overnight_tradable(asset: dict[str, Any] | None) -> bool:
    if not asset:
        return False
    return _bool(asset.get("overnight_tradable"), default=False)


def is_overnight_halted(asset: dict[str, Any] | None) -> bool:
    if not asset:
        return False
    return _bool(asset.get("overnight_halted") or asset.get("halted"), default=False)
