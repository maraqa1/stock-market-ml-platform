from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from stockml.db.connection import get_engine
from stockml.db.connection import _database_url_from_parts
from stockml.db.loaders import _clean_payload, _db_bool, _db_float, _db_int, _db_text, _load_panel, _upsert_rows
from stockml.db.schema import create_all, panel_rows


def test_database_schema_creates_on_sqlite():
    engine = get_engine("sqlite:///:memory:")
    create_all(engine)
    with engine.connect() as conn:
        names = {row[0] for row in conn.exec_driver_sql("select name from sqlite_master where type='table'")}
    assert "price_history" in names
    assert "panel_rows" in names
    assert "model_artifacts" in names


def test_database_url_can_be_built_from_env_parts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("STOCKML_DB_USER", "stockml")
    monkeypatch.setenv("STOCKML_DB_PASSWORD", "secret")
    monkeypatch.setenv("STOCKML_DB_HOST", "localhost")
    monkeypatch.setenv("STOCKML_DB_PORT", "5432")
    monkeypatch.setenv("STOCKML_DB_NAME", "stockml")
    assert _database_url_from_parts() == "postgresql+psycopg2://stockml:secret@localhost:5432/stockml"


def test_typed_db_values_convert_missing_values():
    assert _db_float(float("nan")) is None
    assert _db_float(float("inf")) is None
    assert _db_int(pd.NA) is None
    assert _db_text(float("nan")) is None
    assert _db_bool(float("nan")) is None
    assert _db_int(123.0) == 123
    assert _db_float("12.5") == 12.5
    assert _db_bool("true") is True
    assert _db_bool("false") is False


def test_payload_cleanup_removes_non_finite_json_values():
    payload = _clean_payload({"trailing_pe": float("inf"), "forward_pe": float("-inf"), "beta": 1.2})
    assert payload == {"trailing_pe": None, "forward_pe": None, "beta": 1.2}


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
