from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
GOLD_DIR = DATA_DIR / "gold"
MODEL_OUTPUTS_DIR = DATA_DIR / "model_outputs"
PORTAL_OUTPUTS_DIR = DATA_DIR / "portal_outputs"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_data_dirs() -> None:
    for path in [RAW_DIR, INTERIM_DIR, PROCESSED_DIR, GOLD_DIR, MODEL_OUTPUTS_DIR, PORTAL_OUTPUTS_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def latest_file(directory: Path, pattern: str) -> Optional[Path]:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
