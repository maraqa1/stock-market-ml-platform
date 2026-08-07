from __future__ import annotations

import json

import pandas as pd

from stockml.ai2.bridge import run_ai2_enrichment_bridge
from stockml.ai2.candidate_enrichment import Ai2EnrichmentConfig


def _candidate_file(root):
    out = root / "data" / "portal_outputs"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "execution_ranked_candidates_20260807_120000.csv"
    pd.DataFrame(
        [
            {
                "raw_rank": 1,
                "execution_rank": 1,
                "symbol": "ATRC",
                "side": "buy",
                "source_trade_action": "Long",
                "status": "executable",
                "executable": True,
                "execution_domain": "execution_candidate",
                "order_eligible": True,
                "order_ready": True,
                "execution_pool_eligible": True,
                "final_execution_side": "LONG",
                "research_only": False,
                "all_block_reasons": "",
                "primary_block_reason": "",
                "approved_notional": 250,
                "suggested_quantity": 6,
            }
        ]
    ).to_csv(path, index=False)
    return path


def test_bridge_writes_input_without_api_when_disabled(tmp_path):
    _candidate_file(tmp_path)
    result = run_ai2_enrichment_bridge(
        root=tmp_path,
        config=Ai2EnrichmentConfig(enabled=True, api_enabled=False),
    )

    assert result.status == "api_disabled"
    assert result.input_path.endswith(".csv")
    assert result.merged_path == ""
    assert result.rows == 1


def test_bridge_calls_ai2_and_writes_merged_candidates(tmp_path):
    _candidate_file(tmp_path)

    def transport(url, payload, headers, timeout):
        request_payload = json.loads(payload.decode("utf-8"))
        assert url == "https://ai2.local/enrich"
        assert request_payload["rows"][0]["symbol"] == "ATRC"
        return (
            json.dumps(
                {
                    "rows": [
                        {
                            "symbol": "ATRC",
                            "execution_decision": "Proceed candidate",
                            "latest_eod_date": "2026-08-07",
                            "latest_eod_close": 40.25,
                            "notes": "ok:price_checks_clear",
                        }
                    ]
                }
            ).encode("utf-8"),
            {"content-type": "application/json"},
        )

    result = run_ai2_enrichment_bridge(
        root=tmp_path,
        config=Ai2EnrichmentConfig(enabled=True, api_enabled=True, endpoint_url="https://ai2.local/enrich"),
        transport=transport,
    )

    assert result.status == "ok"
    assert result.ai2_rows == 1
    assert result.ai2_auto_open_allowed == 1
    merged = pd.read_csv(result.merged_path)
    assert bool(merged.loc[0, "ai2_auto_open_allowed"]) is True
    assert merged.loc[0, "ai2_decision_status"] == "proceed"


def test_bridge_rejects_invalid_ai2_response(tmp_path):
    _candidate_file(tmp_path)

    result = run_ai2_enrichment_bridge(
        root=tmp_path,
        config=Ai2EnrichmentConfig(enabled=True, api_enabled=True, endpoint_url="https://ai2.local/enrich"),
        transport=lambda *args: (b'{"rows":[{"decision":"Proceed candidate"}]}', {"content-type": "application/json"}),
    )

    assert result.status == "invalid_response"
    assert result.merged_path == ""
