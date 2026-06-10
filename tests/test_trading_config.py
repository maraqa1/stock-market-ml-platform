from stockml.trading import config as trading_config


def test_higher_risk_paper_defaults(monkeypatch):
    monkeypatch.setattr(trading_config, "_hydrate_environment", lambda: None)
    for name in [
        "STOCKML_ALPACA_MAX_ORDERS",
        "STOCKML_ALPACA_MAX_NOTIONAL_PER_ORDER",
        "STOCKML_ALPACA_MAX_TOTAL_NOTIONAL",
        "STOCKML_MAX_POSITION_PCT",
        "STOCKML_CANDIDATE_POOL_SIZE",
        "STOCKML_ALPACA_OVERNIGHT_TRADING_ENABLED",
        "STOCKML_ALPACA_OVERNIGHT_LIMIT_BUFFER_BPS",
        "STOCKML_ALPACA_OVERNIGHT_CLOSE_REPRICE_AFTER_MINUTES",
        "STOCKML_ALPACA_OVERNIGHT_CLOSE_REPRICE_STEP_BPS",
        "STOCKML_ALPACA_OVERNIGHT_CLOSE_REPRICE_MAX_BUFFER_BPS",
    ]:
        monkeypatch.delenv(name, raising=False)

    cfg = trading_config.alpaca_config()

    assert cfg.max_orders == 20
    assert cfg.max_notional_per_order == 5000
    assert cfg.max_total_notional == 50000
    assert cfg.max_position_pct == 0.05
    assert cfg.candidate_pool_size == 200
    assert cfg.overnight_trading_enabled is False
    assert cfg.overnight_limit_buffer_bps == 50.0
    assert cfg.overnight_close_reprice_after_minutes == 5.0
    assert cfg.overnight_close_reprice_step_bps == 50.0
    assert cfg.overnight_close_reprice_max_buffer_bps == 300.0


def test_overnight_trading_config_loads_from_environment(monkeypatch):
    monkeypatch.setattr(trading_config, "_hydrate_environment", lambda: None)
    monkeypatch.setenv("STOCKML_ALPACA_OVERNIGHT_TRADING_ENABLED", "true")
    monkeypatch.setenv("STOCKML_ALPACA_OVERNIGHT_LIMIT_BUFFER_BPS", "75")
    monkeypatch.setenv("STOCKML_ALPACA_OVERNIGHT_CLOSE_REPRICE_AFTER_MINUTES", "7")
    monkeypatch.setenv("STOCKML_ALPACA_OVERNIGHT_CLOSE_REPRICE_STEP_BPS", "25")
    monkeypatch.setenv("STOCKML_ALPACA_OVERNIGHT_CLOSE_REPRICE_MAX_BUFFER_BPS", "250")

    cfg = trading_config.alpaca_config()

    assert cfg.overnight_trading_enabled is True
    assert cfg.overnight_limit_buffer_bps == 75
    assert cfg.overnight_close_reprice_after_minutes == 7
    assert cfg.overnight_close_reprice_step_bps == 25
    assert cfg.overnight_close_reprice_max_buffer_bps == 250


def test_candidate_pool_size_loads_from_environment(monkeypatch):
    monkeypatch.setattr(trading_config, "_hydrate_environment", lambda: None)
    monkeypatch.setenv("STOCKML_CANDIDATE_POOL_SIZE", "80")

    cfg = trading_config.alpaca_config()

    assert cfg.candidate_pool_size == 80


def test_candidate_pool_size_has_minimum_one(monkeypatch):
    monkeypatch.setattr(trading_config, "_hydrate_environment", lambda: None)
    monkeypatch.setenv("STOCKML_CANDIDATE_POOL_SIZE", "0")

    cfg = trading_config.alpaca_config()

    assert cfg.candidate_pool_size == 1
