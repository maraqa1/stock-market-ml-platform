DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = 'candidate_pool'
    ) THEN
        ALTER TABLE candidate_pool
            DROP CONSTRAINT IF EXISTS ck_candidate_pool_strategy_stream,
            DROP COLUMN IF EXISTS strategy_stream;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = 'positions'
    ) THEN
        ALTER TABLE positions
            DROP CONSTRAINT IF EXISTS ck_positions_strategy_stream,
            DROP COLUMN IF EXISTS max_hold_until,
            DROP COLUMN IF EXISTS must_flatten_at_eod,
            DROP COLUMN IF EXISTS strategy_stream;
    END IF;
END $$;
