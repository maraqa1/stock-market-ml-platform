from __future__ import annotations

from pathlib import Path
from typing import Any

from portal.services.trading_api_service import action_queue_context as _action_queue_context


def action_queue_context(root: Path) -> dict[str, Any]:
    return _action_queue_context(root)
