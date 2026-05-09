from stockml.trading import config as trading_config


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
