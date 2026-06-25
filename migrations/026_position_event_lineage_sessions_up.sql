ALTER TABLE position_events ADD COLUMN IF NOT EXISTS scan_candidate_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS parent_candidate_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS event_session_mode VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS planned_execution_session_mode VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS actual_submission_session_mode VARCHAR(200);
