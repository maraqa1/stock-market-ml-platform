from __future__ import annotations

from pathlib import Path

from stockml.trading.config_fingerprint import fingerprint_files


def test_config_fingerprint_changes_when_file_changes(tmp_path: Path):
    config = tmp_path / "config" / "trading.yaml"
    config.parent.mkdir()
    config.write_text("a: 1\n", encoding="utf-8")

    first = fingerprint_files(["config/trading.yaml"], root=tmp_path)
    config.write_text("a: 2\n", encoding="utf-8")
    second = fingerprint_files(["config/trading.yaml"], root=tmp_path)

    assert first.digest != second.digest
    assert first.files == ["config/trading.yaml"]


def test_config_fingerprint_records_missing_files(tmp_path: Path):
    result = fingerprint_files(["config/missing.yaml"], root=tmp_path)

    assert result.files == []
    assert result.missing_files == ["config/missing.yaml"]
