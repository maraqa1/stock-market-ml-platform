from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.evidence_scope import write_candidate_pool_splits


def write_direction_authority_candidate_splits(
    frame: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> dict[str, Path]:
    return write_candidate_pool_splits(frame, output_dir=output_dir, stamp=stamp)
