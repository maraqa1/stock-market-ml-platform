DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = 'positions'
    ) THEN
        ALTER TABLE positions
            ADD COLUMN IF NOT EXISTS strategy_stream TEXT NOT NULL DEFAULT 'multi_day_forecast',
            ADD COLUMN IF NOT EXISTS must_flatten_at_eod BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS max_hold_until DATE;

        UPDATE positions
        SET strategy_stream = 'multi_day_forecast'
        WHERE strategy_stream IS NULL OR strategy_stream = '';

        ALTER TABLE positions
            DROP CONSTRAINT IF EXISTS ck_positions_strategy_stream;
        ALTER TABLE positions
            ADD CONSTRAINT ck_positions_strategy_stream
            CHECK (strategy_stream IN ('multi_day_forecast', 'same_day_momentum'));
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = 'candidate_pool'
    ) THEN
        ALTER TABLE candidate_pool
            ADD COLUMN IF NOT EXISTS strategy_stream TEXT NOT NULL DEFAULT 'multi_day_forecast';

        UPDATE candidate_pool
        SET strategy_stream = 'multi_day_forecast'
        WHERE strategy_stream IS NULL OR strategy_stream = '';

        ALTER TABLE candidate_pool
            DROP CONSTRAINT IF EXISTS ck_candidate_pool_strategy_stream;
        ALTER TABLE candidate_pool
            ADD CONSTRAINT ck_candidate_pool_strategy_stream
            CHECK (strategy_stream IN ('multi_day_forecast', 'same_day_momentum'));
    END IF;
END $$;
