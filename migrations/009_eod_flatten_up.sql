CREATE TABLE IF NOT EXISTS eod_flatten_log (
    id BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    state TEXT NOT NULL CHECK (state IN ('review','trim','observe','flatten','verify','postclose')),
    position_id TEXT,
    symbol TEXT,
    disposition TEXT CHECK (disposition IN ('weak','stale','winner_hold','none')),
    action_taken TEXT,
    reason TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_eod_flatten_session_date
    ON eod_flatten_log(session_date DESC, logged_at DESC);

CREATE TABLE IF NOT EXISTS eod_summary (
    session_date DATE PRIMARY KEY,
    total_positions INT NOT NULL,
    flattened INT NOT NULL,
    failed_to_flatten INT NOT NULL,
    held_overnight INT NOT NULL,
    notes TEXT
);
