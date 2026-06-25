ALTER TABLE position_events DROP COLUMN IF EXISTS actual_submission_session_mode;
ALTER TABLE position_events DROP COLUMN IF EXISTS planned_execution_session_mode;
ALTER TABLE position_events DROP COLUMN IF EXISTS event_session_mode;
ALTER TABLE position_events DROP COLUMN IF EXISTS parent_candidate_id;
ALTER TABLE position_events DROP COLUMN IF EXISTS scan_candidate_id;
