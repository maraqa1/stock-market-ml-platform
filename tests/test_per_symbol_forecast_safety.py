from pathlib import Path

import pandas as pd

from stockml.trading.per_symbol_forecast.generate import generate_per_symbol_forecast


ROOT = Path(__file__).resolve().parents[1]
FORECAST_ROOT = ROOT / "src" / "stockml" / "trading" / "per_symbol_forecast"
FORBIDDEN_IMPORT_TERMS = ["alpaca", "broker", "submit_order", "autopilot"]


def test_no_broker_or_autopilot_imports_in_forecast_package():
    for path in FORECAST_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        import_lines = [line for line in text.splitlines() if line.strip().startswith(("import ", "from "))]
        assert not any(term in line for term in FORBIDDEN_IMPORT_TERMS for line in import_lines), path


def test_no_writes_to_order_plan_or_candidate_pool(tmp_path: Path):
    source = tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260514_120000.csv"
    source.parent.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAPL", "side": "buy", "candidate_rank": 1, "current_price": 100}]).to_csv(source, index=False)
    before = source.read_bytes()

    result = generate_per_symbol_forecast(tmp_path, stamp="20260514_120001")

    assert Path(result["path"]).exists()
    assert source.read_bytes() == before
    assert not list((tmp_path / "data" / "portal_outputs").glob("08_alpaca_paper_order_plan_*.csv"))


def test_csv_rows_are_diagnostic_only(tmp_path: Path):
    source = tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260514_120000.csv"
    source.parent.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAPL", "side": "buy", "candidate_rank": 1, "current_price": 100}]).to_csv(source, index=False)

    result = generate_per_symbol_forecast(tmp_path, stamp="20260514_120001")
    frame = pd.read_csv(result["path"])

    assert frame["diagnostic_only"].astype(bool).all()
    assert "NOT FOR ORDER SUBMISSION" in frame["diagnostic_notice"].iloc[0]
