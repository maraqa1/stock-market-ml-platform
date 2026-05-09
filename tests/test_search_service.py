import shutil
from pathlib import Path
from uuid import uuid4

import pandas as pd

from portal.services import search as search_service


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def reset_cache() -> None:
    search_service._SYMBOL_CACHE = None


def temp_root() -> Path:
    root = Path(".pytest_workspace") / f"search_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_search_empty_query_returns_no_groups():
    root = temp_root()
    reset_cache()
    try:
        assert search_service.search("", root=root)["groups"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_search_symbol_prefix_returns_position_signal_and_reference_groups():
    root = temp_root()
    reset_cache()
    try:
        write_csv(
            root / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
            [{"symbol": "TSLA", "side": "long", "unrealized_plpc": 0.02}],
        )
        write_csv(
            root / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
            [{"symbol": "TSLX", "trade_action": "Long", "risk_adjusted_score": 0.4}],
        )
        payload = search_service.search("tsl", root=root, limit=5)
        groups = {group["key"]: group["items"] for group in payload["groups"]}
        assert groups["positions"][0]["symbol"] == "TSLA"
        assert groups["signals"][0]["symbol"] == "TSLX"
        assert any(item["symbol"] == "TSLA" for item in groups.get("reference", [])) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_search_full_symbol_reference_fallback():
    root = temp_root()
    reset_cache()
    try:
        payload = search_service.search("TSLA", root=root, limit=5)
        groups = {group["key"]: group["items"] for group in payload["groups"]}
        assert groups["reference"][0]["symbol"] == "TSLA"
        assert groups["reference"][0]["url"] == "/symbols/TSLA"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_search_run_id_returns_only_runs_group():
    root = temp_root()
    reset_cache()
    try:
        payload = search_service.search("2026-05-09-A", root=root, limit=5)
        assert [group["key"] for group in payload["groups"]] == ["runs"]
        assert payload["groups"][0]["items"][0]["run_id"] == "2026-05-09-A"
    finally:
        shutil.rmtree(root, ignore_errors=True)
