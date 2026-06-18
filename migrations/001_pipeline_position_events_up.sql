CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id VARCHAR(100) PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL,
    current_stage VARCHAR(50),
    error TEXT,
    triggered_by VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    run_id VARCHAR(100) NOT NULL,
    stage_name VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL,
    output_count INTEGER DEFAULT 0,
    output_metadata JSON,
    error TEXT,
    PRIMARY KEY (run_id, stage_name),
    CONSTRAINT ck_pipeline_stages_stage_name CHECK (
        stage_name IN ('yahoo', 'gold', 'model', 'candidates', 'selection', 'submitted')
    )
);

CREATE TABLE IF NOT EXISTS position_events (
    id SERIAL PRIMARY KEY,
    position_id VARCHAR(200) NOT NULL,
    event_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type VARCHAR(50) NOT NULL,
    source VARCHAR(100) NOT NULL,
    details JSON,
    CONSTRAINT ck_position_events_event_type CHECK (
        event_type IN (
            'scored',
            'ranked',
            'selected',
            'submitted',
            'filled',
            'partial',
            'monitor_safe',
            'monitor_watch',
            'monitor_close',
            'monitor_rotate',
            'operator_keep',
            'operator_close',
            'operator_override',
            'broker_rejected',
            'guardrail_blocked',
            'anti_churn_blocked',
            'candidate_scanned',
            'candidate_blocked',
            'candidate_submitted',
            'candidate_skipped_duplicate',
            'candidate_skipped_anti_churn',
            'candidate_skipped_meta_label',
            'candidate_skipped_not_overnight_tradable'
        )
    )
);

CREATE INDEX IF NOT EXISTS ix_position_events_position_event_at
    ON position_events (position_id, event_at DESC);

CREATE INDEX IF NOT EXISTS ix_position_events_event_at
    ON position_events (event_at DESC);
