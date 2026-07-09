from __future__ import annotations

import pandas as pd

from stockml.trading.candidate_pool_export import write_direction_authority_candidate_splits


def test_direction_authority_candidate_pool_split_outputs(tmp_path):
    frame = pd.DataFrame(
        [
            {"symbol": "EXEC", "execution_domain": "execution_candidate", "status": "executable", "executable": True, "research_only": False},
            {"symbol": "WATCH", "execution_domain": "watch_candidate", "status": "watch", "executable": False, "research_only": False},
            {"symbol": "SHADOW", "execution_domain": "shadow_observation", "status": "research_only", "executable": False, "research_only": True},
            {"symbol": "BLOCK", "execution_domain": "blocked_candidate", "status": "blocked", "executable": False, "research_only": False},
        ]
    )

    paths = write_direction_authority_candidate_splits(frame, output_dir=tmp_path, stamp="20260709_120000")

    assert set(paths) == {"execution_candidate_pool", "watch_candidate_pool", "blocked_candidate_pool", "shadow_observation_pool"}
    assert pd.read_csv(paths["execution_candidate_pool"])["symbol"].tolist() == ["EXEC"]
    assert pd.read_csv(paths["watch_candidate_pool"])["symbol"].tolist() == ["WATCH"]
    assert pd.read_csv(paths["blocked_candidate_pool"])["symbol"].tolist() == ["BLOCK"]
    assert pd.read_csv(paths["shadow_observation_pool"])["symbol"].tolist() == ["SHADOW"]
