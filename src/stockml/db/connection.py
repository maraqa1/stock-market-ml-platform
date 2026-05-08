from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def database_url(required: bool = True) -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    if required:
        raise RuntimeError("DATABASE_URL is not set. Example: postgresql+psycopg2://stockml:stockml@localhost:5432/stockml")
    return None


def get_engine(url: Optional[str] = None, required: bool = True) -> Optional[Engine]:
    resolved = url or database_url(required=required)
    if not resolved:
        return None
    return create_engine(resolved, pool_pre_ping=True, future=True)

