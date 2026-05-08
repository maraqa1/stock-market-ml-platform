from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

metadata = MetaData()

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


def create_all(engine) -> None:
    metadata.create_all(engine)

