import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.execution_engine import AlpacaExecutionEngine
from stockml.trading.risk_manager import ExecutionRiskPolicy, RiskManager


def config():
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=True,
        extended_hours=False,
        max_orders=10,
        max_notional_per_order=1000.0,
        max_total_notional=10000.0,
        min_trade_price=5.0,
        max_sector_fraction=1.0,
        min_side_probability=0.55,
        min_abs_probability_edge=0.05,
        min_intraday_volume=100000,
        min_market_cap=300000000.0,
        min_risk_adjusted_score=0.005,
        transaction_cost_bps=10.0,
    )


class FakeClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, payload):
        self.submitted.append(payload)
        return {"id": "order-1", "status": "accepted"}


def rec(**overrides):
    row = {
        "symbol": "FLEX",
        "signal": "LONG",
        "confidence": 0.72,
        "rank_score": 0.9,
        "sector": "Technology",
        "last_price": 100,
        "avg_dollar_volume": 100_000_000,
        "recommended_notional": 1000,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
    }
    row.update(overrides)
    return row


def engine(mode="dry_run", client=None, risk_manager=None):
    return AlpacaExecutionEngine(config=config(), mode=mode, client=client or FakeClient(), risk_manager=risk_manager, use_sdk=False)


def test_confidence_below_threshold_skips():
    report = engine().execute(pd.DataFrame([rec(confidence=0.4)]))
    assert report.iloc[0]["decision"] == "skipped"
    assert report.iloc[0]["reason"] == "confidence_below_threshold"


def test_duplicate_open_order_blocked():
    rm = RiskManager(ExecutionRiskPolicy(), open_orders=[{"symbol": "FLEX", "side": "buy"}])
    report = engine(risk_manager=rm).execute(pd.DataFrame([rec()]))
    assert report.iloc[0]["decision"] == "rejected"
    assert report.iloc[0]["reason"] == "duplicate_open_order"


def test_max_exposure_blocked():
    rm = RiskManager(ExecutionRiskPolicy(max_single_position_exposure=1000), positions=[{"symbol": "FLEX", "market_value": 1000}])
    report = engine(risk_manager=rm).execute(pd.DataFrame([rec()]))
    assert report.iloc[0]["reason"] == "max_position_exposure_reached"


def test_dry_run_does_not_submit():
    client = FakeClient()
    report = engine(mode="dry_run", client=client).execute(pd.DataFrame([rec()]))
    assert report.iloc[0]["decision"] == "dry_run"
    assert client.submitted == []


def test_paper_mode_submits_after_validation():
    client = FakeClient()
    report = engine(mode="paper", client=client).execute(pd.DataFrame([rec()]))
    assert report.iloc[0]["decision"] == "submitted"
    assert client.submitted[0]["order_class"] == "bracket"
