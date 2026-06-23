from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sqlalchemy.engine import Engine

from portal.services.journal import JournalFilters, LINEAGE_EXPORT_COLUMNS, iter_csv, query


def query_activity_journal(filters: JournalFilters, *, target: Engine | None = None, root: Path | None = None) -> dict:
    return query(filters, target=target, root=root)


def iter_activity_journal_csv(filters: JournalFilters, *, target: Engine | None = None, root: Path | None = None) -> Iterable[str]:
    return iter_csv(filters, target=target, root=root)
