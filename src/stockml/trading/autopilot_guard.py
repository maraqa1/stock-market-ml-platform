from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stockml.common.paths import PORTAL_OUTPUTS_DIR


AUTOPILOT_BASKET_BLOCK_REASON = "paper_autopilot_running_blocks_basket_submission"


def autopilot_state_path() -> Path:
    return PORTAL_OUTPUTS_DIR / "paper_autopilot_state.json"


def load_autopilot_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or autopilot_state_path()
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def autopilot_blocks_basket_submission(path: Path | None = None) -> tuple[bool, str]:
    state = load_autopilot_state(path)
    if str(state.get("status", "")).lower() == "running" and str(state.get("mode", "")).lower() == "paper_autopilot":
        return True, AUTOPILOT_BASKET_BLOCK_REASON
    return False, ""
