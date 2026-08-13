from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AI2EnrichmentResult:
    status: str
    raw_candidate_file: Path | None = None
    enriched_candidate_file: Path | None = None
    canonical_enriched_file: Path | None = None
    run_id: str = ""
    timestamp: str = ""
    adapter_version: str = ""
    row_count: int = 0
    reason: str = ""
    intake_status: str = ""
    intake_reason: str = ""
    audit_events: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def fail_safe(self) -> bool:
        return not self.ok
