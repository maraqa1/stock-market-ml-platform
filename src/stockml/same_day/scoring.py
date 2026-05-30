from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT


MODEL_ROOT = PROJECT_ROOT / "data" / "models" / "same_day"


@dataclass(frozen=True)
class SameDayModelBundle:
    model_id: str
    long_model: Any
    short_model: Any
    feature_list: list[str]


@dataclass(frozen=True)
class SameDayScore:
    direction: str
    long_probability: float
    short_probability: float
    continuation_probability: float
    reversal_probability: float
    same_day_confidence: float


class ConstantProbabilityModel:
    def __init__(self, probability: float):
        self.probability = float(probability)

    def predict_proba(self, rows):
        return [[1.0 - self.probability, self.probability] for _ in range(len(rows))]


def _probability(model: Any, features: dict[str, Any], feature_list: list[str]) -> float:
    row = pd.DataFrame([{name: features.get(name, 0.0) for name in feature_list}])
    if hasattr(model, "predict_proba"):
        raw = model.predict_proba(row)
        return float(raw[0][1])
    if callable(model):
        return float(model(features))
    if isinstance(model, (int, float)):
        return float(model)
    return 0.5


def score_features(features: dict[str, Any], bundle: SameDayModelBundle) -> SameDayScore:
    long_p = max(0.0, min(1.0, _probability(bundle.long_model, features, bundle.feature_list)))
    short_p = max(0.0, min(1.0, _probability(bundle.short_model, features, bundle.feature_list)))
    direction = "long" if long_p >= short_p else "short"
    continuation = max(long_p, short_p)
    return SameDayScore(
        direction=direction,
        long_probability=long_p,
        short_probability=short_p,
        continuation_probability=continuation,
        reversal_probability=1.0 - continuation,
        same_day_confidence=abs(long_p - short_p),
    )


def load_model_bundle(path: Path | None = None) -> SameDayModelBundle:
    model_path = path or MODEL_ROOT / "promoted_model.pkl"
    if not model_path.exists():
        return SameDayModelBundle(
            model_id="constant-baseline",
            long_model=ConstantProbabilityModel(0.5),
            short_model=ConstantProbabilityModel(0.5),
            feature_list=[],
        )
    with model_path.open("rb") as fh:
        payload = pickle.load(fh)
    if isinstance(payload, SameDayModelBundle):
        return payload
    return SameDayModelBundle(
        model_id=str(payload.get("model_id") or model_path.stem),
        long_model=payload["long_model"],
        short_model=payload["short_model"],
        feature_list=list(payload.get("feature_list") or []),
    )
