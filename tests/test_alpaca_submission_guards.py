from stockml.trading.submission_guards import SubmissionContext, validate_order


class FakeClient:
    def __init__(self, asset=None):
        self.asset = asset or {"tradable": True, "status": "active", "shortable": True}

    def get_asset(self, symbol):
        return self.asset


def test_validate_order_rejects_unhealthy_account():
    allowed, reason = validate_order(
        {"symbol": "AAA", "client_order_id": "id-1", "notional": 100, "side": "buy"},
        FakeClient(),
        SubmissionContext(healthy=False, message="account_not_ready"),
        set(),
    )
    assert allowed is False
    assert reason == "account_not_ready"


def test_validate_order_rejects_duplicate_open_symbol():
    allowed, reason = validate_order(
        {"symbol": "AAA", "client_order_id": "id-1", "notional": 100, "side": "buy"},
        FakeClient(),
        SubmissionContext(healthy=True, buying_power=1000, open_orders=[{"symbol": "AAA"}]),
        set(),
    )
    assert allowed is False
    assert reason == "symbol_already_has_open_order"


def test_validate_order_rejects_untradable_asset():
    allowed, reason = validate_order(
        {"symbol": "AAA", "client_order_id": "id-1", "notional": 100, "side": "buy"},
        FakeClient(asset={"tradable": False, "status": "active"}),
        SubmissionContext(healthy=True, buying_power=1000),
        set(),
    )
    assert allowed is False
    assert reason == "asset_not_tradable"


def test_validate_order_passes_and_tracks_client_id():
    seen = set()
    allowed, reason = validate_order(
        {"symbol": "AAA", "client_order_id": "id-1", "notional": 100, "side": "buy"},
        FakeClient(),
        SubmissionContext(healthy=True, buying_power=1000),
        seen,
    )
    assert allowed is True
    assert reason == "submission_preflight_passed"
    assert seen == {"id-1"}
