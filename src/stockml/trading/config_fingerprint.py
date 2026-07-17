from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from stockml.common.paths import PROJECT_ROOT


DEFAULT_CONFIG_FILES = [
    "config/autopilot.yaml",
    "config/eod.yaml",
    "config/monitor.yaml",
    "config/risk_policy.yaml",
    "config/same_day.yaml",
    "config/session_modes.yaml",
    "config/trading.yaml",
]

STRATEGY_CONFIG_FILES = [
    "config/autopilot.yaml",
    "config/monitor.yaml",
    "config/same_day.yaml",
    "config/session_modes.yaml",
    "config/trading.yaml",
]

GATE_CONFIG_FILES = [
    "config/autopilot.yaml",
    "config/risk_policy.yaml",
    "config/session_modes.yaml",
    "config/trading.yaml",
]


@dataclass(frozen=True)
class ConfigFingerprint:
    name: str
    digest: str
    files: list[str]
    missing_files: list[str]


def _normalise_paths(paths: Iterable[str | Path], root: Path) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = Path(raw)
        out.append(path if path.is_absolute() else root / path)
    return sorted(out, key=lambda p: str(p).replace("\\", "/"))


def fingerprint_files(paths: Iterable[str | Path], *, root: Path | None = None, name: str = "config") -> ConfigFingerprint:
    base = root or PROJECT_ROOT
    digest = hashlib.sha256()
    files: list[str] = []
    missing: list[str] = []
    for path in _normalise_paths(paths, base):
        rel = path.relative_to(base).as_posix() if path.is_relative_to(base) else path.as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        if not path.exists():
            missing.append(rel)
            digest.update(b"<missing>")
            continue
        files.append(rel)
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return ConfigFingerprint(name=name, digest=digest.hexdigest(), files=files, missing_files=missing)


def config_fingerprints(*, root: Path | None = None) -> dict[str, ConfigFingerprint]:
    return {
        "config": fingerprint_files(DEFAULT_CONFIG_FILES, root=root, name="config"),
        "strategy": fingerprint_files(STRATEGY_CONFIG_FILES, root=root, name="strategy"),
        "gate": fingerprint_files(GATE_CONFIG_FILES, root=root, name="gate"),
    }


def fingerprint_json(fingerprints: dict[str, ConfigFingerprint]) -> str:
    payload = {
        name: {
            "digest": fp.digest,
            "files": fp.files,
            "missing_files": fp.missing_files,
        }
        for name, fp in sorted(fingerprints.items())
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
