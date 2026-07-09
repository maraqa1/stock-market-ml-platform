from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import execution_ranked_auto_open_candidates


def _write_ranked(root: Path, rows: list[dict]) -> Path:
    out = root / "data" / "portal_outputs"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "execution_ranked_candidates_20260709_120000.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _row(**overrides):
    row = {
        "raw_rank": 1,
        "execution_rank": 1,
        "symbol": "DFTX",
        "side": "buy",
        "source_trade_action": "Long",
        "status": "executable",
        "executable": True,
        "execution_domain": "execution_candidate",
        "execution_eligible": True,
        "final_execution_side": "LONG",
        "research_only": False,
        "all_block_reasons": "",
    }
    row.update(overrides)
    return row


def test_execution_engine_rejects_non_execution_domains(tmp_path: Path):
    _write_ranked(
        tmp_path,
        [
            _row(symbol="WATCH", execution_domain="watch_candidate", execution_eligible=False),
            _row(symbol="BLOCK", execution_domain="blocked_candidate", execution_eligible=False),
            _row(symbol="SHADOW", execution_domain="shadow_observation", execution_eligible=False),
        ],
    )

    assert execution_ranked_auto_open_candidates(root=tmp_path) == []


def test_execution_engine_accepts_only_execution_candidate_domain(tmp_path: Path):
    _write_ranked(
        tmp_path,
        [
            _row(symbol="SHADOW", execution_domain="shadow_observation", execution_eligible=False),
            _row(symbol="DFTX", execution_domain="execution_candidate", execution_eligible=True),
        ],
    )

    rows = execution_ranked_auto_open_candidates(root=tmp_path)
    assert [row["symbol"] for row in rows] == ["DFTX"]

