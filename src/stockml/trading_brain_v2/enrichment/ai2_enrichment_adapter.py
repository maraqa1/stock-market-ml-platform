from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Protocol

from stockml.common.paths import PROJECT_ROOT


def _timestamp_key(path: Path) -> str:
    match = re.search(r"(\d{8}_\d{6})", path.name)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class AdapterEnrichmentResult:
    status: str
    enriched_file: Path | None = None
    reason: str = ""
    adapter_version: str = ""


class AI2EnrichmentAdapter(Protocol):
    adapter_version: str

    def enrich(self, raw_candidate_file: Path, *, output_dir: Path, run_id: str) -> AdapterEnrichmentResult:
        ...


class ExistingFileAI2EnrichmentAdapter:
    """Adapter for the current local workflow where AI2 writes a shortlist artifact.

    This deliberately does not fabricate an enriched file. If a repository-side AI2
    API or script is added later, it should implement the same adapter protocol.
    """

    adapter_version = "existing_file_v1"

    def __init__(self, *, search_root: Path | str | None = None):
        self.search_root = Path(search_root) if search_root is not None else PROJECT_ROOT

    def enrich(self, raw_candidate_file: Path, *, output_dir: Path, run_id: str) -> AdapterEnrichmentResult:
        candidates = self._candidate_outputs(raw_candidate_file, output_dir=output_dir)
        if not candidates:
            return AdapterEnrichmentResult(
                status="failed",
                reason="ai2_enrichment_mechanism_missing_or_output_not_found",
                adapter_version=self.adapter_version,
            )
        return AdapterEnrichmentResult(
            status="ok",
            enriched_file=max(candidates, key=lambda path: (path.stat().st_mtime, _timestamp_key(path), path.name)),
            adapter_version=self.adapter_version,
        )

    def _candidate_outputs(self, raw_candidate_file: Path, *, output_dir: Path) -> list[Path]:
        raw_stem = raw_candidate_file.stem
        search_dirs = [
            raw_candidate_file.parent,
            output_dir,
            self.search_root / "data" / "ai2",
            self.search_root / "data" / "portal_outputs",
        ]
        patterns = [
            f"{raw_stem}.shortlist.csv",
            f"{raw_stem}*.shortlist.csv",
            "ai2_candidate_input_*.shortlist.csv",
            "ai2_enriched_candidates_*.csv",
        ]
        found: list[Path] = []
        for directory in search_dirs:
            if not directory.exists():
                continue
            for pattern in patterns:
                found.extend(path for path in directory.glob(pattern) if path.is_file())
        raw_resolved = raw_candidate_file.resolve()
        return sorted({path.resolve() for path in found if path.resolve() != raw_resolved})
