from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from stockml.common.paths import PROJECT_ROOT

PROFILE_FILE = PROJECT_ROOT / "config" / "pipeline_profiles.yaml"


def load_profiles(path: Path = PROFILE_FILE) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing pipeline profiles file: {path}")
    payload = yaml.safe_load(path.read_text()) or {}
    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"No profiles defined in {path}")
    return profiles


def load_profile(name: str, path: Path = PROFILE_FILE) -> Dict[str, Any]:
    profiles = load_profiles(path)
    if name not in profiles:
        available = ", ".join(sorted(profiles))
        raise KeyError(f"Unknown profile '{name}'. Available profiles: {available}")
    profile = dict(profiles[name])
    profile["name"] = name
    return profile

