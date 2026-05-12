from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

metadata = MetaData()

PIPELINE_STAGE_NAMES = ("yahoo", "gold", "model", "candidates", "selection", "submitted")
POSITION_EVENT_TYPES = (
    "scored",
    "ranked",
    "selected",
    "submitted",
    "filled",
    "partial",
    "monitor_safe",
    "monitor_watch",
    "monitor_close",
    "monitor_rotate",
    "operator_keep",
    "operator_close",
    "operator_override",
    "broker_rejected",
    "guardrail_blocked",
)
KILL_SWITCH_EVENT_TYPES = ("tripped", "resumed")
INTRADAY_VERDICTS = ("allow_long", "allow_short", "hold", "block", "data_unavailable")
SHADOW_WOULD_TRADE_STATUS = ("pending", "evaluated", "superseded", "cancelled")
PROMOTION_DRY_RUN_EVENT_TYPES = ("confirmed",)
EOD_STATES = ("review", "trim", "observe", "flatten", "verify", "postclose")
EOD_DISPOSITIONS = ("weak", "stale", "winner_hold", "none")
INTRADAY_CANDIDATE_SNAPSHOT_STATUS = ("ok", "data_unavailable", "provider_error")


def _in_values(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("pipeline_name", String(100), nullable=False),
    Column("profile", String(100)),
    Column("status", String(50), nullable=False),
    Column("source_file", Text),
    Column("row_count", Integer, default=0),
    Column("message", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", String(100), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True)),
    Column("status", String(50), nullable=False, default="running"),
    Column("current_stage", String(50)),
    Column("error", Text),
    Column("triggered_by", String(100)),
)

pipeline_stages = Table(
    "pipeline_stages",
    metadata,
    Column("run_id", String(100), primary_key=True),
    Column("stage_name", String(50), primary_key=True),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("status", String(50), nullable=False, default="pending"),
    Column("output_count", Integer, default=0),
    Column("output_metadata", JSON),
    Column("error", Text),
    CheckConstraint(_in_values("stage_name", PIPELINE_STAGE_NAMES), name="ck_pipeline_stages_stage_name"),
)

position_events = Table(
    "position_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("position_id", String(200), nullable=False),
    Column("event_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("event_type", String(50), nullable=False),
    Column("source", String(100), nullable=False),
    Column("details", JSON),
    CheckConstraint(_in_values("event_type", POSITION_EVENT_TYPES), name="ck_position_events_event_type"),
    Index("ix_position_events_position_event_at", "position_id", "event_at"),
    Index("ix_position_events_event_at", "event_at"),
)

equity_universe = Table(
    "equity_universe",
    metadata,
    Column("symbol", String(50), nullable=False),
    Column("yahoo_ticker", String(50), nullable=False),
    Column("company", Text),
    Column("listing_exchange", String(50)),
    Column("is_tradable_common_stock_candidate", Boolean),
    Column("exclude_reason", Text),
    Column("payload", JSON),
    Column("source_file", Text),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("yahoo_ticker", name="uq_equity_universe_yahoo_ticker"),
)

price_history = Table(
    "price_history",
    metadata,
    Column("date", Date, nullable=False),
    Column("ticker", String(50), nullable=False),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("adj_close", Float),
    Column("volume", BigInteger),
    Column("source", String(100)),
    Column("payload", JSON),
    Column("source_file", Text),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("date", "ticker", name="uq_price_history_date_ticker"),
)

metadata_enriched = Table(
    "metadata_enriched",
    metadata,
    Column("ticker", String(50), nullable=False),
    Column("company", Text),
    Column("exchange", String(50)),
    Column("sector", Text),
    Column("industry", Text),
    Column("market_cap", Float),
    Column("metadata_status", String(100)),
    Column("payload", JSON),
    Column("source_file", Text),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("ticker", name="uq_metadata_enriched_ticker"),
)

panel_rows = Table(
    "panel_rows",
    metadata,
    Column("dataset", String(50), nullable=False),
    Column("date", Date, nullable=False),
    Column("ticker", String(50), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("source_file", Text),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("dataset", "date", "ticker", name="uq_panel_rows_dataset_date_ticker"),
)

sentiment_panel = Table(
    "sentiment_panel",
    metadata,
    Column("date", Date, nullable=False),
    Column("ticker", String(50), nullable=False),
    Column("article_count", Integer),
    Column("sentiment_score_mean", Float),
    Column("sentiment_source", Text),
    Column("sentiment_status", String(100)),
    Column("payload", JSON),
    Column("source_file", Text),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("date", "ticker", name="uq_sentiment_panel_date_ticker"),
)

model_artifacts = Table(
    "model_artifacts",
    metadata,
    Column("artifact_type", String(100), nullable=False),
    Column("artifact_key", String(200), nullable=False),
    Column("date", Date),
    Column("ticker", String(50)),
    Column("payload", JSON, nullable=False),
    Column("source_file", Text),
    Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("artifact_type", "artifact_key", name="uq_model_artifacts_type_key"),
)

shortlist_snapshots = Table(
    "shortlist_snapshots",
    metadata,
    Column("run_id", String(100), ForeignKey("pipeline_runs.run_id"), primary_key=True),
    Column("rank", Integer, nullable=False),
    Column("symbol", String(50), primary_key=True),
    Column("bias", String(20), nullable=False),
    Column("score", Float, nullable=False),
    Column("expected_edge", Float),
    Column("sector", Text),
    Column("in_basket", Boolean, nullable=False, default=False),
    Column("excluded_reason", Text),
    CheckConstraint(_in_values("bias", ("long", "short", "neutral")), name="ck_shortlist_snapshots_bias"),
    Index("ix_shortlist_run_rank", "run_id", "rank"),
)

output_prediction = Table(
    "output_prediction",
    metadata,
    Column("symbol", String(50), primary_key=True),
    Column("prediction_date", Date, primary_key=True),
    Column("horizon_days", Integer, primary_key=True),
    Column("outperform_probability", Float),
    Column("expected_excess_return", Float),
    Column("confidence", Float),
    Column("model_version", String(100), nullable=False),
    Column("run_timestamp", DateTime(timezone=True)),
)

output_outcome = Table(
    "output_outcome",
    metadata,
    Column("symbol", String(50), primary_key=True),
    Column("prediction_date", Date, primary_key=True),
    Column("evaluation_date", Date, nullable=False),
    Column("predicted_excess_return", Float),
    Column("actual_excess_return", Float),
    Column("outperformed", Boolean),
    Column("model_version", String(100), nullable=False),
)

model_runs = Table(
    "model_runs",
    metadata,
    Column("model_version", String(100), primary_key=True),
    Column("trained_at", DateTime(timezone=True), nullable=False),
    Column("oos_hit_pct", Float),
    Column("oos_excess_pct", Float),
    Column("promoted", Boolean, nullable=False, default=False),
    Column("notes", Text),
)

model_folds = Table(
    "model_folds",
    metadata,
    Column("model_version", String(100), ForeignKey("model_runs.model_version"), primary_key=True),
    Column("period", String(100), primary_key=True),
    Column("train_rows", BigInteger, nullable=False),
    Column("test_rows", BigInteger, nullable=False),
    Column("hit_pct", Float),
    Column("excess_pct", Float),
    Column("notes", Text),
)

model_feature_importance = Table(
    "model_feature_importance",
    metadata,
    Column("model_version", String(100), ForeignKey("model_runs.model_version"), primary_key=True),
    Column("feature_name", String(200), primary_key=True),
    Column("importance", Float, nullable=False),
)

kill_switch_events = Table(
    "kill_switch_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("switch_name", String(100), nullable=False),
    Column("event_type", String(20), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("payload", JSON, nullable=False),
    Column("operator_id", String(100)),
    Column("notes", Text),
    CheckConstraint(_in_values("event_type", KILL_SWITCH_EVENT_TYPES), name="ck_kill_switch_events_event_type"),
    Index("ix_kse_switch_occurred", "switch_name", "occurred_at"),
)

intraday_decisions = Table(
    "intraday_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("decided_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("symbol", String(50), nullable=False),
    Column("bar_close_at", DateTime(timezone=True), nullable=False),
    Column("verdict", String(30), nullable=False),
    Column("block_reason", String(100)),
    Column("gate_version", String(50), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
    Column("nightly_signal", JSON),
    Column("features", JSON, nullable=False),
    Column("contributing", JSON),
    CheckConstraint(_in_values("verdict", INTRADAY_VERDICTS), name="ck_intraday_decisions_verdict"),
    Index("ix_intraday_decisions_decided_at", "decided_at"),
    Index("ix_intraday_decisions_symbol_decided_at", "symbol", "decided_at"),
    Index("ix_intraday_decisions_verdict", "verdict", "decided_at"),
    Index("ix_intraday_decisions_block_reason", "block_reason", "decided_at"),
)

shadow_would_trades = Table(
    "shadow_would_trades",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("decision_id", Integer, ForeignKey("intraday_decisions.id"), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False),
    Column("symbol", String(50), nullable=False),
    Column("side", String(20), nullable=False),
    Column("entry_price", Float, nullable=False),
    Column("estimated_entry_slippage_bps", Float, nullable=False),
    Column("nightly_score", Float),
    Column("gate_version", String(50), nullable=False),
    Column("evaluation_date", Date, nullable=False),
    Column("status", String(20), nullable=False, default="pending"),
    CheckConstraint(_in_values("side", ("long", "short")), name="ck_shadow_would_trades_side"),
    CheckConstraint(_in_values("status", SHADOW_WOULD_TRADE_STATUS), name="ck_shadow_would_trades_status"),
    Index("ix_shadow_wt_pending", "evaluation_date"),
    Index("ix_shadow_wt_symbol_decided", "symbol", "decided_at"),
)

shadow_outcomes = Table(
    "shadow_outcomes",
    metadata,
    Column("would_trade_id", Integer, ForeignKey("shadow_would_trades.id"), primary_key=True),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column("exit_price", Float, nullable=False),
    Column("raw_return_pct", Float, nullable=False),
    Column("cost_bps", Float, nullable=False),
    Column("net_return_pct", Float, nullable=False),
    Column("spy_return_pct", Float, nullable=False),
    Column("net_excess_pct", Float, nullable=False),
    Column("outperformed", Boolean, nullable=False),
)

promotion_evaluations = Table(
    "promotion_evaluations",
    metadata,
    Column("evaluated_at", DateTime(timezone=True), primary_key=True),
    Column("gate_version", String(50), nullable=False),
    Column("criteria_met", Boolean, nullable=False),
    Column("criteria_results", JSON, nullable=False),
    Column("notes", Text),
)

promotion_dry_runs = Table(
    "promotion_dry_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("confirmed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("operator_id", String(100), nullable=False),
    Column("symbol", String(50), nullable=False),
    Column("side", String(20), nullable=False),
    Column("notes", Text, nullable=False),
    CheckConstraint(_in_values("side", ("long", "short")), name="ck_promotion_dry_runs_side"),
)

eod_flatten_log = Table(
    "eod_flatten_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("session_date", Date, nullable=False),
    Column("logged_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("state", String(20), nullable=False),
    Column("position_id", String(200)),
    Column("symbol", String(50)),
    Column("disposition", String(30)),
    Column("action_taken", String(100)),
    Column("reason", Text),
    Column("details", JSON, nullable=False, default=dict),
    CheckConstraint(_in_values("state", EOD_STATES), name="ck_eod_flatten_log_state"),
    CheckConstraint(f"disposition IS NULL OR {_in_values('disposition', EOD_DISPOSITIONS)}", name="ck_eod_flatten_log_disposition"),
    Index("ix_eod_flatten_session_date", "session_date", "logged_at"),
)

eod_summary = Table(
    "eod_summary",
    metadata,
    Column("session_date", Date, primary_key=True),
    Column("total_positions", Integer, nullable=False),
    Column("flattened", Integer, nullable=False),
    Column("failed_to_flatten", Integer, nullable=False),
    Column("held_overnight", Integer, nullable=False),
    Column("notes", Text),
)

intraday_candidate_snapshots = Table(
    "intraday_candidate_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("snapshot_at", DateTime(timezone=True), nullable=False),
    Column("bar_close_at", DateTime(timezone=True), nullable=False),
    Column("symbol", String(50), nullable=False),
    Column("nightly_score", Float),
    Column("nightly_bias", String(20)),
    Column("is_held", Boolean, nullable=False, default=False),
    Column("bid", Float),
    Column("ask", Float),
    Column("last_price", Float),
    Column("spread_bps", Float),
    Column("quote_age_sec", Integer),
    Column("dollar_volume_today", Float),
    Column("liquidity_ratio", Float),
    Column("trend_5m_pct", Float),
    Column("trend_15m_pct", Float),
    Column("trend_30m_pct", Float),
    Column("vwap_today", Float),
    Column("distance_from_vwap_bps", Float),
    Column("intraday_range_position", Float),
    Column("volatility_burst", Boolean, nullable=False, default=False),
    Column("sector_etf_trend_5m_pct", Float),
    Column("market_aligned", Boolean),
    Column("status", String(30), nullable=False),
    Column("details", JSON, nullable=False, default=dict),
    CheckConstraint(_in_values("nightly_bias", ("long", "short", "neutral")), name="ck_ics_nightly_bias"),
    CheckConstraint(_in_values("status", INTRADAY_CANDIDATE_SNAPSHOT_STATUS), name="ck_ics_status"),
    UniqueConstraint("symbol", "bar_close_at", name="uq_ics_symbol_bar_close_at"),
    Index("ix_ics_snapshot_at", "snapshot_at"),
    Index("ix_ics_symbol_snapshot", "symbol", "snapshot_at"),
)


def create_all(engine) -> None:
    metadata.create_all(engine)
