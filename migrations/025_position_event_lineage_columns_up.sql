ALTER TABLE position_events ADD COLUMN IF NOT EXISTS pipeline_run_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS cycle_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS signal_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS candidate_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS event_key VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS client_order_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS trade_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS exit_decision_id VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS order_intent VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS strategy_mode VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS session_mode VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS candidate_source VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS model_version VARCHAR(200);
ALTER TABLE position_events ADD COLUMN IF NOT EXISTS lineage_warning TEXT;

UPDATE position_events
SET
  pipeline_run_id = COALESCE(pipeline_run_id, details->>'pipeline_run_id'),
  cycle_id = COALESCE(cycle_id, details->>'cycle_id'),
  signal_id = COALESCE(signal_id, details->>'signal_id'),
  candidate_id = COALESCE(candidate_id, details->>'candidate_id'),
  event_key = COALESCE(event_key, details->>'event_key'),
  client_order_id = COALESCE(client_order_id, details->>'client_order_id'),
  broker_order_id = COALESCE(broker_order_id, details->>'broker_order_id', details->>'order_id'),
  trade_id = COALESCE(trade_id, details->>'trade_id'),
  exit_decision_id = COALESCE(exit_decision_id, details->>'exit_decision_id'),
  order_intent = COALESCE(order_intent, details->>'order_intent'),
  strategy_mode = COALESCE(strategy_mode, details->>'strategy_mode'),
  session_mode = COALESCE(session_mode, details->>'session_mode'),
  candidate_source = COALESCE(candidate_source, details->>'candidate_source'),
  model_version = COALESCE(model_version, details->>'model_version'),
  lineage_warning = COALESCE(lineage_warning, details->>'lineage_warning')
WHERE details IS NOT NULL;

UPDATE position_events
SET candidate_id = 'cand-' || substr(md5(
        COALESCE(cycle_id, details->>'cycle_id', '') || '|' ||
        upper(COALESCE(details->>'symbol', split_part(position_id, ':', 2), '')) || '|' ||
        COALESCE(candidate_source, details->>'candidate_source', source, 'paper_order_plan')
    ), 1, 16)
WHERE candidate_id IS NULL
  AND COALESCE(cycle_id, details->>'cycle_id', '') <> ''
  AND COALESCE(details->>'symbol', split_part(position_id, ':', 2), '') <> ''
  AND event_type IN ('selected', 'candidate_scanned', 'candidate_blocked', 'candidate_submitted', 'candidate_skipped_duplicate', 'candidate_skipped_anti_churn', 'candidate_skipped_meta_label', 'candidate_skipped_not_overnight_tradable');

UPDATE position_events
SET session_mode = 'regular_session'
WHERE session_mode IS NULL
  AND event_type IN ('selected', 'candidate_scanned', 'candidate_blocked', 'candidate_submitted', 'candidate_skipped_duplicate', 'candidate_skipped_anti_churn', 'candidate_skipped_meta_label', 'candidate_skipped_not_overnight_tradable');

UPDATE position_events
SET order_intent = CASE lower(COALESCE(details->>'side', ''))
    WHEN 'buy' THEN 'open_long'
    WHEN 'sell' THEN 'open_short'
    ELSE order_intent
END
WHERE order_intent IS NULL
  AND event_type IN ('selected', 'candidate_submitted');
