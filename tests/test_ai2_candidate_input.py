from __future__ import annotations

import pandas as pd

from stockml.ai2.candidate_input import AI2_INPUT_COLUMNS, build_ai2_candidate_input, write_ai2_candidate_input


def test_ai2_candidate_input_preserves_execution_context_and_ordering(tmp_path):
    candidates = pd.DataFrame(
        [
            {
                "raw_rank": 10,
                "execution_rank": 2,
                "symbol": "FIVE",
                "final_execution_side": "LONG",
                "status": "executable",
                "executable": True,
                "execution_domain": "execution_candidate",
                "order_eligible": True,
                "order_ready": True,
                "approved_notional": 500,
                "primary_block_reason": "",
            },
            {
                "raw_rank": 9,
                "execution_rank": 1,
                "symbol": "ATRC",
                "final_execution_side": "LONG",
                "status": "executable",
                "executable": True,
                "execution_domain": "execution_candidate",
                "order_eligible": True,
                "order_ready": True,
                "approved_notional": 250,
                "primary_block_reason": "",
            },
            {
                "raw_rank": 1,
                "execution_rank": "",
                "symbol": "JUNK",
                "final_execution_side": "NONE",
                "status": "blocked",
                "executable": False,
                "execution_domain": "blocked_candidate",
                "order_eligible": False,
                "order_ready": False,
                "primary_block_reason": "market_cap_missing",
            },
        ]
    )

    out = build_ai2_candidate_input(candidates, limit=2)

    assert out.columns.tolist() == AI2_INPUT_COLUMNS
    assert out["symbol"].tolist() == ["ATRC", "FIVE"]
    assert out.loc[out["symbol"].eq("ATRC"), "execution_domain"].iloc[0] == "execution_candidate"
    assert out.loc[out["symbol"].eq("FIVE"), "approved_notional"].iloc[0] == 500

    path = write_ai2_candidate_input(candidates, output_dir=tmp_path, limit=3, stamp="20260807_120000")
    written = pd.read_csv(path)
    assert path.name == "ai2_candidate_input_20260807_120000.csv"
    assert written["symbol"].tolist() == ["ATRC", "FIVE", "JUNK"]


def test_ai2_candidate_input_rejects_missing_symbol():
    try:
        build_ai2_candidate_input(pd.DataFrame([{"execution_rank": 1}]))
    except ValueError as exc:
        assert "symbol" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected missing symbol to raise")
