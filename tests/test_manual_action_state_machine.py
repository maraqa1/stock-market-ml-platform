from pathlib import Path

from stockml.trading.config import AlpacaConfig
from stockml.trading.manual_position_actions import apply_manual_position_action


def cfg(*, submit=False, live=False, overnight=False):
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="paper",
        submit_orders=submit,
        extended_hours=False,
        max_orders=20,
        max_notional_per_order=5000.0,
        max_total_notional=50000.0,
        min_trade_price=1.0,
        max_sector_fraction=1.0,
        min_side_probability=0.0,
        min_abs_probability_edge=0.0,
        min_intraday_volume=0,
        min_market_cap=0.0,
        min_risk_adjusted_score=-1.0,
        transaction_cost_bps=10.0,
        paper_trading_enabled=True,
        live_trading_enabled=live,
        overnight_trading_enabled=overnight,
    )


class Client:
    def close_position(self, symbol):
        return {"id": "order-1", "client_order_id": "cid-1", "status": "accepted"}
    def list_positions(self):
        return [{"symbol": "FLEX", "qty": "2", "current_price": "10"}]
    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "overnight_tradable": True}
    def submit_order(self, order):
        return {"id": "order-2", "client_order_id": order["client_order_id"], "status": "accepted"}


def test_manual_keep_and_close_have_single_final_states(tmp_path: Path):
    keep = apply_manual_position_action("FLEX", "keep", config=cfg(), client=Client(), output_path=tmp_path / "a.csv")
    close = apply_manual_position_action("FLEX", "close", config=cfg(submit=True), client=Client(), output_path=tmp_path / "b.csv")
    assert keep["message"] == "keep_recorded"
    assert close["message"] == "close_submitted_regular"
    assert keep["message"] != close["message"]


def test_dry_run_disabled_and_close_submitted_cannot_both_be_final_states(tmp_path: Path):
    result = apply_manual_position_action("FLEX", "close", config=cfg(submit=False), client=Client(), output_path=tmp_path / "a.csv")
    assert result["status"] == "dry_run"
    assert result["message"] == "close_blocked_submit_disabled"
    assert result["message"] != "close_submitted_regular"


def test_live_trading_disabled_and_close_submitted_cannot_both_be_final_states(tmp_path: Path):
    result = apply_manual_position_action("FLEX", "close", config=cfg(submit=True, live=True), client=Client(), output_path=tmp_path / "a.csv")
    assert result["status"] == "rejected"
    assert result["message"] == "close_blocked_live_trading_disabled"
    assert result["message"] != "close_submitted_regular"


def test_manual_overnight_limit_has_one_final_state(tmp_path: Path):
    result = apply_manual_position_action("FLEX", "close", config=cfg(submit=True, overnight=True), client=Client(), output_path=tmp_path / "a.csv")
    assert result["message"] == "close_submitted_overnight_limit"
