import shutil
from pathlib import Path
from uuid import uuid4

import pandas as pd

from portal.services.symbol_detail import get


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def temp_root() -> Path:
    root = Path(".pytest_workspace") / f"symbol_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_symbol_detail_current_position_only():
    root = temp_root()
    try:
        write_csv(
            root / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
            [
                {
                    "symbol": "TSLA",
                    "side": "long",
                    "qty": 2,
                    "avg_entry_price": 240,
                    "current_price": 245,
                    "market_value": 490,
                    "cost_basis": 480,
                    "unrealized_pl": 10,
                    "unrealized_plpc": 0.0208,
                }
            ],
        )
        detail = get("TSLA", root)
        assert detail is not None
        assert detail["position"]["qty"] == 2
        assert detail["today_signal"] is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_symbol_detail_signal_only():
    root = temp_root()
    try:
        write_csv(
            root / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
            [
                {
                    "candidate_rank": 7,
                    "symbol": "TSLA",
                    "company": "Tesla, Inc.",
                    "sector": "Consumer Cyclical",
                    "trade_action": "Long",
                    "risk_adjusted_score": 0.71,
                    "expected_trade_return": 0.016,
                    "order_eligible": True,
                }
            ],
        )
        detail = get("TSLA", root)
        assert detail is not None
        assert detail["position"] is None
        assert detail["today_signal"]["rank"] == 7
        assert detail["today_signal"]["bias"] == "long"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_symbol_detail_both_position_and_signal():
    root = temp_root()
    try:
        write_csv(root / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv", [{"symbol": "TSLA", "qty": 1}])
        write_csv(root / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv", [{"symbol": "TSLA", "trade_action": "Short"}])
        detail = get("TSLA", root)
        assert detail is not None
        assert detail["position"] is not None
        assert detail["today_signal"] is not None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_symbol_detail_unknown_returns_none():
    root = temp_root()
    try:
        assert get("INVALID", root) is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_symbol_detail_common_reference_symbol_renders_without_current_artifacts():
    root = temp_root()
    try:
        detail = get("TSLA", root)
        assert detail is not None
        assert detail["symbol"] == "TSLA"
        assert detail["name"] == "Tesla, Inc."
        assert detail["position"] is None
        assert detail["today_signal"] is None
    finally:
        shutil.rmtree(root, ignore_errors=True)
