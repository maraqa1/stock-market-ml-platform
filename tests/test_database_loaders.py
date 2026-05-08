from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from stockml.db.connection import get_engine
from stockml.db.loaders import _load_panel, _upsert_rows
from stockml.db.schema import create_all, panel_rows


def test_database_schema_creates_on_sqlite():
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)
    with engine.connect() as conn:
        names = {row[0] for row in conn.exec_driver_sql("select name from sqlite_master where type='table'")}
    assert "price_history" in names
    assert "panel_rows" in names
    assert "model_artifacts" in names


def test_panel_loader_upserts_rows():
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)
    path = Path("tmp_database_loader_gold.csv")
    try:
        pd.DataFrame(
            [
                {"date": "2024-01-02", "ticker": "AAA", "value": 1.0},
                {"date": "2024-01-02", "ticker": "BBB", "value": 2.0},
            ]
        ).to_csv(path, index=False)

        with engine.begin() as conn:
            assert _load_panel(conn, "gold_dataset", path) == 2
            assert _load_panel(conn, "gold_dataset", path) == 2
            rows = conn.execute(select(panel_rows)).fetchall()
        assert len(rows) == 2
    finally:
        path.unlink(missing_ok=True)


def test_generic_upsert_replaces_existing_row():
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)
    with engine.begin() as conn:
        _upsert_rows(
            conn,
            panel_rows,
            [{"dataset": "gold_dataset", "date": date(2024, 1, 2), "ticker": "AAA", "payload": {"value": 1}}],
            ["dataset", "date", "ticker"],
        )
        _upsert_rows(
            conn,
            panel_rows,
            [{"dataset": "gold_dataset", "date": date(2024, 1, 2), "ticker": "AAA", "payload": {"value": 2}}],
            ["dataset", "date", "ticker"],
        )
        rows = conn.execute(select(panel_rows.c.payload)).fetchall()
    assert len(rows) == 1
    assert rows[0][0]["value"] == 2
