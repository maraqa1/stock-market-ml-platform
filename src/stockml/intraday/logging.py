from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stockml.common.paths import PROJECT_ROOT


def _now() -> datetime:
    return datetime.now(timezone.utc)


def intraday_log(event_type: str, payload: dict[str, Any] | None = None, root: Path | None = None, now: datetime | None = None) -> Path:
    stamp = now or _now()
    base = Path(root) if root is not None else PROJECT_ROOT
    path = base / "data" / "trading" / "intraday" / f"intraday_log_{stamp.strftime('%Y%m%d')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "event_at": stamp.isoformat(timespec="seconds"),
        "event_type": str(event_type),
        "payload": payload or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path

