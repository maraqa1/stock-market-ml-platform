CREATE TABLE IF NOT EXISTS model_runs (
    model_version TEXT PRIMARY KEY,
    trained_at TIMESTAMPTZ NOT NULL,
    oos_hit_pct DOUBLE PRECISION,
    oos_excess_pct DOUBLE PRECISION,
    promoted BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS model_folds (
    model_version TEXT NOT NULL REFERENCES model_runs(model_version),
    period TEXT NOT NULL,
    train_rows BIGINT NOT NULL,
    test_rows BIGINT NOT NULL,
    hit_pct DOUBLE PRECISION,
    excess_pct DOUBLE PRECISION,
    notes TEXT,
    PRIMARY KEY (model_version, period)
);

CREATE TABLE IF NOT EXISTS model_feature_importance (
    model_version TEXT NOT NULL REFERENCES model_runs(model_version),
    feature_name TEXT NOT NULL,
    importance DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (model_version, feature_name)
);
