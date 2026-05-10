from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from stockml.models.meta_label_features import build_feature_matrix, selected_meta_features
from stockml.models.meta_label_targets import add_meta_label_targets, trade_examples


CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "meta_labeling.yaml"


@dataclass(frozen=True)
class MetaLabelConfig:
    enabled: bool = True
    min_meta_label_probability: float = 0.60
    transaction_cost_bps: float = 10.0
    embargo_days: int = 5
    min_training_signals: int = 500
    model_type: str = "hist_gradient_boosting"


@dataclass
class MetaLabelFit:
    model: HistGradientBoostingClassifier | None
    features: list[str]
    matrix_columns: list[str]
    fitted: bool
    reason: str = ""


def _coerce(value: str) -> Any:
    text = str(value).strip()
    lower = text.lower()
    if lower in {"true", "yes", "y"}:
        return True
    if lower in {"false", "no", "n"}:
        return False
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def load_meta_label_config(path: Path | None = None) -> MetaLabelConfig:
    config_path = path or CONFIG_PATH
    values: dict[str, Any] = {}
    if config_path.exists():
        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = _coerce(value)
    return MetaLabelConfig(**{key: value for key, value in values.items() if key in MetaLabelConfig.__annotations__})


def train_meta_label_model(frame: pd.DataFrame, config: MetaLabelConfig | None = None) -> MetaLabelFit:
    cfg = config or load_meta_label_config()
    labeled = trade_examples(add_meta_label_targets(frame, cfg.transaction_cost_bps))
    if len(labeled) < cfg.min_training_signals:
        return MetaLabelFit(None, selected_meta_features(frame), [], False, "min_training_signals_not_met")
    if labeled["meta_label"].nunique() < 2:
        return MetaLabelFit(None, selected_meta_features(frame), [], False, "single_class_meta_labels")
    features = selected_meta_features(labeled)
    x, columns = build_feature_matrix(labeled, features)
    if not columns:
        return MetaLabelFit(None, features, [], False, "no_usable_meta_features")
    model = HistGradientBoostingClassifier(random_state=42, max_iter=120, learning_rate=0.05)
    model.fit(x, labeled["meta_label"].astype(int))
    return MetaLabelFit(model, features, columns, True)


def predict_meta_label_probability(fit: MetaLabelFit, frame: pd.DataFrame) -> pd.Series:
    if not fit.fitted or fit.model is None or not fit.matrix_columns:
        return pd.Series(pd.NA, index=frame.index, dtype="object")
    x, _ = build_feature_matrix(frame, fit.features, fit.matrix_columns)
    return pd.Series(fit.model.predict_proba(x)[:, 1], index=frame.index, dtype="float64")


def add_meta_label_predictions(frame: pd.DataFrame, fit: MetaLabelFit, config: MetaLabelConfig | None = None) -> pd.DataFrame:
    cfg = config or load_meta_label_config()
    out = frame.copy()
    probability = predict_meta_label_probability(fit, out)
    out["meta_label_probability"] = probability
    out["meta_label_decision"] = "Skip Trade"
    out["meta_label_reason"] = fit.reason or "meta_label_probability_below_threshold"
    trade_mask = out.get("trade_action", pd.Series("", index=out.index)).astype(str).str.lower().isin({"long", "short"})
    pass_mask = trade_mask & pd.to_numeric(out["meta_label_probability"], errors="coerce").fillna(-1).ge(cfg.min_meta_label_probability)
    out.loc[pass_mask, "meta_label_decision"] = "Take Trade"
    out.loc[pass_mask, "meta_label_reason"] = "meta_label_gate_passed"
    out.loc[~trade_mask, "meta_label_reason"] = "primary_signal_not_trade"
    if not fit.fitted:
        out["meta_label_reason"] = fit.reason or "meta_label_model_not_available"
    return out
