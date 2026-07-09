from __future__ import annotations

import pandas as pd

from stockml.trading.candidate_pool_export import write_direction_authority_candidate_splits


def test_direction_authority_candidate_pool_split_outputs(tmp_path):
    frame = pd.DataFrame(
        [
            {"symbol": "EXEC", "status": "executable", "executable": True, "research_only": False},
            {"symbol": "RESEARCH", "status": "research_only", "executable": False, "research_only": True},
            {"symbol": "BLOCK", "status": "blocked", "executable": False, "research_only": False},
        ]
    )

    paths = write_direction_authority_candidate_splits(frame, output_dir=tmp_path, stamp="20260709_120000")

    assert set(paths) == {"research_candidate_pool", "execution_candidate_pool", "blocked_candidate_pool"}
    assert pd.read_csv(paths["execution_candidate_pool"])["symbol"].tolist() == ["EXEC"]
    assert pd.read_csv(paths["research_candidate_pool"])["symbol"].tolist() == ["RESEARCH"]
    assert pd.read_csv(paths["blocked_candidate_pool"])["symbol"].tolist() == ["BLOCK"]
