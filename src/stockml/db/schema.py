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


def create_all(engine) -> None:
    metadata.create_all(engine)
