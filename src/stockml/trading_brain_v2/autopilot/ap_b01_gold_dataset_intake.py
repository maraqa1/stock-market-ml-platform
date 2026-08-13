from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd

from stockml.common.paths import PROJECT_ROOT
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


DEFAULT_CANDIDATE_PATTERNS = (
    "ai2_candidate_input_*.shortlist.csv",
    "ai2_enriched_candidates_*.csv",
    "execution_ranked_candidates_*.shortlist.csv",
    "execution_ranked_candidates_*.csv",
)


def _artifact_timestamp_key(path: Path) -> str:
    match = re.search(r"(\d{8}_\d{6})", path.name)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class CandidateIntakeResult:
    path: Path | None
    records: list[dict]
    status: str
    reason: str = ""

    @property
    def row_count(self) -> int:
        return len(self.records)


class GoldDatasetIntakeBlock(PlaceholderBlock):
    block_id = "AP-B01"
    name = "Gold Dataset Intake"

    def evaluate(self, payload: dict | None = None) -> BrainBlockResult:
        result = self.load_candidate_file(path=(payload or {}).get("path"), root=(payload or {}).get("root"))
        return BrainBlockResult(
            block_id=self.block_id,
            status=result.status,
            decision="NO_ACTION",
            reason=result.reason or "candidate_intake_complete",
            details={"path": str(result.path or ""), "row_count": result.row_count},
        )

    def latest_candidate_file(self, *, root: Path | str | None = None) -> Path | None:
        base = Path(root) if root is not None else PROJECT_ROOT
        search_dirs = [
            base / "data" / "portal_outputs",
            base / "data" / "ai2",
            base / "data" / "trading",
        ]
        candidates: list[Path] = []
        for directory in search_dirs:
            if not directory.exists():
                continue
            for pattern in DEFAULT_CANDIDATE_PATTERNS:
                candidates.extend(path for path in directory.glob(pattern) if path.is_file())
        return max(candidates, key=lambda path: (path.stat().st_mtime, _artifact_timestamp_key(path), path.name)) if candidates else None

    def load_candidate_file(self, *, path: Path | str | None = None, root: Path | str | None = None) -> CandidateIntakeResult:
        source = Path(path) if path is not None else self.latest_candidate_file(root=root)
        if source is None:
            return CandidateIntakeResult(path=None, records=[], status="missing_data", reason="candidate_file_missing")
        if not source.exists():
            return CandidateIntakeResult(path=source, records=[], status="missing_data", reason="candidate_file_not_found")
        if source.suffix.lower() != ".csv":
            return CandidateIntakeResult(path=source, records=[], status="unsupported", reason="unsupported_candidate_file_type")
        try:
            frame = pd.read_csv(source, low_memory=False)
        except Exception as exc:
            return CandidateIntakeResult(path=source, records=[], status="error", reason=f"candidate_file_read_error:{exc}")
        records = frame.fillna("").to_dict("records")
        for row in records:
            row.setdefault("source_file", str(source))
        return CandidateIntakeResult(path=source, records=records, status="ok")
