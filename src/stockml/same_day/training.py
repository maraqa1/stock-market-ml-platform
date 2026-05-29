from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.same_day.labels import compute_continuation_label, realized_move_bps


FEATURE_COLUMNS = [
    "return_5m",
    "return_15m",
    "return_30m",
    "relative_volume",
    "dollar_volume_15m",
    "vwap_distance_bps_5m",
    "intraday_range_position",
    "time_of_day_bucket",
]


@dataclass(frozen=True)
class Fold:
    fold: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def _utc(value: Any) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _num(value: Any, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(parsed) else parsed)


def _bar_at_or_before(frame: pd.DataFrame, stamp: pd.Timestamp) -> pd.Series | None:
    rows = frame[frame["timestamp"] <= stamp]
    if rows.empty:
        return None
    return rows.iloc[-1]


def time_of_day_bucket(decision_time: pd.Timestamp) -> int:
    stamp = _utc(decision_time).tz_convert("America/New_York")
    minutes = stamp.hour * 60 + stamp.minute
    if minutes < 10 * 60:
        return 0
    if minutes < 11 * 60:
        return 1
    if minutes < 14 * 60:
        return 2
    if minutes < 15 * 60:
        return 3
    return 4


def compute_minimal_features(
    bars: pd.DataFrame,
    decision_time: pd.Timestamp,
    *,
    average_volume_by_bar: dict[str, float] | None = None,
) -> dict[str, float] | None:
    """Compute SPEC 72 minimal features with a one-bar look-ahead buffer."""

    if bars.empty:
        return None
    frame = bars.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    decision = _utc(decision_time)
    latest_allowed = decision - pd.Timedelta(minutes=5)
    history = frame[frame["timestamp"] <= latest_allowed].copy()
    if len(history) < 7:
        return None

    last = history.iloc[-1]
    prev_15 = _bar_at_or_before(history, latest_allowed - pd.Timedelta(minutes=15))
    prev_30 = _bar_at_or_before(history, latest_allowed - pd.Timedelta(minutes=30))
    prev_35 = _bar_at_or_before(history, latest_allowed - pd.Timedelta(minutes=35))
    if prev_15 is None or prev_30 is None or prev_35 is None:
        return None

    last_open = _num(last.get("open"))
    last_close = _num(last.get("close"))
    close_15_start = _num(prev_15.get("open"))
    close_30_start = _num(prev_30.get("open"))
    return_5m = np.log(last_close / last_open) if last_open > 0 and last_close > 0 else 0.0
    return_15m = np.log(last_close / close_15_start) if close_15_start > 0 and last_close > 0 else 0.0
    return_30m = np.log(last_close / close_30_start) if close_30_start > 0 and last_close > 0 else 0.0

    key = latest_allowed.tz_convert("America/New_York").strftime("%H:%M")
    avg_volume = (average_volume_by_bar or {}).get(key)
    if avg_volume is None:
        same_bar = history[history["timestamp"].dt.tz_convert("America/New_York").dt.strftime("%H:%M").eq(key)]
        avg_volume = float(pd.to_numeric(same_bar["volume"], errors="coerce").mean() or 0)
    relative_volume = _num(last.get("volume")) / avg_volume if avg_volume else 0.0

    window_15 = history[history["timestamp"] >= latest_allowed - pd.Timedelta(minutes=15)]
    dollar_volume_15m = float((pd.to_numeric(window_15["close"], errors="coerce") * pd.to_numeric(window_15["volume"], errors="coerce")).sum())

    vwap = pd.to_numeric(history.get("vwap"), errors="coerce")
    if vwap.isna().all():
        vol = pd.to_numeric(history["volume"], errors="coerce").fillna(0)
        pxv = pd.to_numeric(history["close"], errors="coerce").fillna(0) * vol
        vwap_value = float(pxv.sum() / vol.sum()) if vol.sum() else 0.0
    else:
        vwap_value = float(vwap.dropna().iloc[-1])
    vwap_distance = (last_close - vwap_value) / vwap_value * 10_000 if vwap_value else 0.0

    high = pd.to_numeric(history["high"], errors="coerce").max()
    low = pd.to_numeric(history["low"], errors="coerce").min()
    range_position = 0.5 if pd.isna(high) or pd.isna(low) or high == low else float((last_close - low) / (high - low))

    return {
        "return_5m": float(return_5m),
        "return_15m": float(return_15m),
        "return_30m": float(return_30m),
        "relative_volume": float(relative_volume),
        "dollar_volume_15m": float(dollar_volume_15m),
        "vwap_distance_bps_5m": float(vwap_distance),
        "intraday_range_position": float(max(0.0, min(1.0, range_position))),
        "time_of_day_bucket": float(time_of_day_bucket(decision)),
    }


def candidate_signals_from_bars(
    bars: pd.DataFrame,
    *,
    threshold_bps: int = 50,
    horizon_minutes: int = 30,
) -> pd.DataFrame:
    frame = bars.copy()
    if frame.empty:
        return pd.DataFrame()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    rows: list[dict[str, Any]] = []
    for symbol, group in frame.sort_values(["symbol", "timestamp"]).groupby("symbol"):
        by_time = group.copy().reset_index(drop=True)
        features_by_time = _minimal_feature_frame(by_time)
        for index in range(8, max(8, len(by_time) - 8)):
            decision_time = by_time.at[index, "timestamp"]
            feature_row = features_by_time.iloc[index]
            if not bool(feature_row.get("__eligible", False)):
                continue
            features = {column: float(feature_row[column]) for column in FEATURE_COLUMNS}
            long_fired = features["return_5m"] > 0 and features["return_15m"] > 0 and features["relative_volume"] > 1.5
            short_fired = features["return_5m"] < 0 and features["return_15m"] < 0 and features["relative_volume"] > 1.5
            for direction, fired in (("long", long_fired), ("short", short_fired)):
                if not fired:
                    continue
                label = compute_continuation_label(by_time, decision_time, direction, horizon_minutes, threshold_bps)
                move = realized_move_bps(by_time, decision_time, direction, horizon_minutes)
                if label is None or move is None:
                    continue
                spread_bps = _num(by_time.loc[by_time["timestamp"].le(_utc(decision_time) - pd.Timedelta(minutes=5)), "spread_bps"].iloc[-1], 0.0) if "spread_bps" in by_time.columns else 0.0
                market_impact_bps = 5.0
                cost = 2 * spread_bps + market_impact_bps
                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp": _utc(decision_time),
                        "direction": direction,
                        "label": int(label),
                        "realized_move_bps": float(move),
                        "estimated_round_trip_cost_bps": float(cost),
                        "net_bps": float(move - cost),
                        "liquidity_tier": liquidity_tier(features["dollar_volume_15m"]),
                        **features,
                    }
                )
    return pd.DataFrame(rows)


def _minimal_feature_frame(group: pd.DataFrame) -> pd.DataFrame:
    """Vectorized SPEC 72 feature rows keyed by decision-time row index."""

    out = pd.DataFrame(index=group.index)
    stamp = pd.to_datetime(group["timestamp"], utc=True)
    ny_stamp = stamp.dt.tz_convert("America/New_York")
    tod = ny_stamp.dt.time
    latest = group.shift(1)

    open_px = pd.to_numeric(latest["open"], errors="coerce")
    close_px = pd.to_numeric(latest["close"], errors="coerce")
    open_15 = pd.to_numeric(group["open"].shift(4), errors="coerce")
    open_30 = pd.to_numeric(group["open"].shift(7), errors="coerce")
    volume = pd.to_numeric(latest["volume"], errors="coerce")

    out["return_5m"] = np.where((open_px > 0) & (close_px > 0), np.log(close_px / open_px), 0.0)
    out["return_15m"] = np.where((open_15 > 0) & (close_px > 0), np.log(close_px / open_15), 0.0)
    out["return_30m"] = np.where((open_30 > 0) & (close_px > 0), np.log(close_px / open_30), 0.0)

    bar_time = ny_stamp.shift(1).dt.strftime("%H:%M")
    avg_volume = volume.groupby(bar_time).transform("mean")
    out["relative_volume"] = np.where(avg_volume > 0, volume / avg_volume, 0.0)

    close = pd.to_numeric(group["close"], errors="coerce")
    vol = pd.to_numeric(group["volume"], errors="coerce")
    out["dollar_volume_15m"] = (close * vol).shift(1).rolling(4, min_periods=1).sum().fillna(0.0)

    if "vwap" in group.columns and not pd.to_numeric(group["vwap"], errors="coerce").isna().all():
        vwap_value = pd.to_numeric(group["vwap"], errors="coerce").shift(1)
    else:
        prev_vol = vol.shift(1).fillna(0)
        cumulative_vol = prev_vol.cumsum()
        cumulative_pxv = (close.shift(1).fillna(0) * prev_vol).cumsum()
        vwap_value = cumulative_pxv / cumulative_vol.replace(0, np.nan)
    out["vwap_distance_bps_5m"] = np.where(vwap_value > 0, (close_px - vwap_value) / vwap_value * 10_000, 0.0)

    high = pd.to_numeric(group["high"], errors="coerce").shift(1).cummax()
    low = pd.to_numeric(group["low"], errors="coerce").shift(1).cummin()
    spread = high - low
    out["intraday_range_position"] = np.where(spread > 0, (close_px - low) / spread, 0.5)
    out["intraday_range_position"] = out["intraday_range_position"].clip(0.0, 1.0)
    out["time_of_day_bucket"] = stamp.map(time_of_day_bucket).astype(float)

    out = out.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["__eligible"] = (
        (tod >= pd.Timestamp("10:00").time())
        & (tod <= pd.Timestamp("15:00").time())
        & group["open"].shift(7).notna()
    )
    return out


def liquidity_tier(dollar_volume_15m: float) -> str:
    if dollar_volume_15m >= 10_000_000:
        return "high"
    if dollar_volume_15m >= 2_000_000:
        return "medium"
    return "thin"


def walk_forward_folds(frame: pd.DataFrame, folds: int = 8) -> list[Fold]:
    if frame.empty:
        return []
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    weeks = sorted(data["timestamp"].dt.to_period("W").astype(str).unique().tolist())
    if len(weeks) < 2:
        return []
    out: list[Fold] = []
    max_folds = min(folds, len(weeks) - 1)
    for index in range(1, max_folds + 1):
        train_weeks = weeks[:index]
        test_week = weeks[index]
        train_rows = data[data["timestamp"].dt.to_period("W").astype(str).isin(train_weeks)]
        test_rows = data[data["timestamp"].dt.to_period("W").astype(str).eq(test_week)]
        if train_rows.empty or test_rows.empty:
            continue
        out.append(
            Fold(
                fold=len(out) + 1,
                train_start=train_rows["timestamp"].min(),
                train_end=train_rows["timestamp"].max(),
                test_start=test_rows["timestamp"].min(),
                test_end=test_rows["timestamp"].max(),
            )
        )
    return out


def split_holdout(frame: pd.DataFrame, holdout_days: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = frame.copy()
    if data.empty:
        return data, data
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    days = sorted(data["timestamp"].dt.date.unique())
    holdout_set = set(days[-holdout_days:]) if len(days) > holdout_days else {days[-1]}
    holdout = data[data["timestamp"].dt.date.isin(holdout_set)].copy()
    train = data[~data["timestamp"].dt.date.isin(holdout_set)].copy()
    return train, holdout


def balance_classes(frame: pd.DataFrame, random_state: int = 42) -> tuple[pd.DataFrame, dict[str, Any]]:
    if frame.empty or "label" not in frame.columns:
        return frame.copy(), {"pre_downsample": {}, "post_downsample": {}}
    counts = frame["label"].value_counts().to_dict()
    if len(counts) < 2:
        return frame.copy(), {"pre_downsample": counts, "post_downsample": counts}
    min_count = min(counts.values())
    balanced = (
        frame.groupby("label", group_keys=False)
        .apply(lambda group: group.sample(n=min_count, random_state=random_state))
        .sample(frac=1, random_state=random_state)
        .reset_index(drop=True)
    )
    return balanced, {"pre_downsample": counts, "post_downsample": balanced["label"].value_counts().to_dict()}


def train_probability_model(train: pd.DataFrame):
    x = train[FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
    y = train["label"].astype(int)
    if y.nunique() < 2:
        return None
    try:
        from lightgbm import LGBMClassifier  # type: ignore

        model = LGBMClassifier(
            objective="binary",
            n_estimators=120,
            learning_rate=0.05,
            num_leaves=15,
            random_state=42,
            n_jobs=1,
            verbosity=-1,
        )
    except Exception:
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(max_iter=120, learning_rate=0.05, max_leaf_nodes=15, random_state=42)
    model.fit(x, y)
    return model


def predict_probabilities(model: Any, frame: pd.DataFrame) -> np.ndarray:
    x = frame[FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
    if model is None:
        return np.full(len(frame), 0.5)
    if hasattr(model, "predict_proba"):
        return np.asarray(model.predict_proba(x))[:, 1]
    return np.asarray(model.predict(x), dtype=float)


def calibrated_fold_predictions(frame: pd.DataFrame, folds: Iterable[Fold]) -> pd.DataFrame:
    from sklearn.isotonic import IsotonicRegression

    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    outputs = []
    for fold in folds:
        train = data[(data["timestamp"] >= fold.train_start) & (data["timestamp"] <= fold.train_end)]
        test = data[(data["timestamp"] >= fold.test_start) & (data["timestamp"] <= fold.test_end)]
        balanced, _ = balance_classes(train)
        model = train_probability_model(balanced)
        raw = predict_probabilities(model, test)
        if len(np.unique(balanced["label"])) == 2 and len(raw) >= 3:
            try:
                calibrator = IsotonicRegression(out_of_bounds="clip")
                in_sample = predict_probabilities(model, balanced)
                calibrator.fit(in_sample, balanced["label"].astype(int))
                prob = calibrator.predict(raw)
            except Exception:
                prob = raw
        else:
            prob = raw
        outputs.append(test.assign(fold=fold.fold, predicted_probability=prob))
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def holdout_predictions(train: pd.DataFrame, holdout: pd.DataFrame) -> pd.DataFrame:
    balanced, _ = balance_classes(train)
    model = train_probability_model(balanced)
    probs = predict_probabilities(model, holdout)
    return holdout.copy().assign(predicted_probability=probs)


def auc_score(y_true: pd.Series, y_prob: pd.Series) -> float:
    from sklearn.metrics import roc_auc_score

    if len(pd.Series(y_true).dropna().unique()) < 2:
        return 0.5
    return float(roc_auc_score(y_true, y_prob))


def calibration_table(frame: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["bucket", "count", "mean_predicted_probability", "realized_continuation_rate"])
    out = frame.copy()
    out["bucket"] = pd.qcut(out["predicted_probability"].rank(method="first"), q=min(bins, len(out)), labels=False, duplicates="drop")
    return (
        out.groupby("bucket")
        .agg(
            count=("label", "size"),
            mean_predicted_probability=("predicted_probability", "mean"),
            realized_continuation_rate=("label", "mean"),
        )
        .reset_index()
    )


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    display = frame.copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            display[column] = display[column].map(lambda value: f"{float(value):.4f}")
    headers = [str(column) for column in display.columns]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in display.fillna("").astype(str).to_dict("records"):
        lines.append("| " + " | ".join(row[column] for column in headers) + " |")
    return "\n".join(lines)


def economic_metrics(frame: pd.DataFrame, threshold: float) -> dict[str, float]:
    selected = frame[frame["predicted_probability"] >= threshold].copy()
    if selected.empty:
        return {
            "threshold": threshold,
            "signals_taken": 0,
            "hit_rate": 0.0,
            "mean_realized_move_bps": 0.0,
            "mean_net_bps": 0.0,
            "t_stat": 0.0,
            "p05": 0.0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p95": 0.0,
            "max_drawdown": 0.0,
        }
    net = pd.to_numeric(selected["net_bps"], errors="coerce").fillna(0)
    returns = net / 10_000 * 0.01
    equity = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1
    std = float(net.std(ddof=1)) if len(net) > 1 else 0.0
    return {
        "threshold": threshold,
        "signals_taken": float(len(selected)),
        "hit_rate": float(selected["label"].mean()),
        "mean_realized_move_bps": float(selected["realized_move_bps"].mean()),
        "mean_net_bps": float(net.mean()),
        "t_stat": float(net.mean() / (std / np.sqrt(len(net)))) if std else 0.0,
        "p05": float(net.quantile(0.05)),
        "p25": float(net.quantile(0.25)),
        "p50": float(net.quantile(0.50)),
        "p75": float(net.quantile(0.75)),
        "p95": float(net.quantile(0.95)),
        "max_drawdown": float(abs(drawdown.min())) if len(drawdown) else 0.0,
    }


def classify_verdict(metrics: list[dict[str, float]]) -> str:
    if not metrics:
        return "RED"
    best_mean = max(row["mean_net_bps"] for row in metrics)
    by_060 = next((row for row in metrics if abs(row["threshold"] - 0.60) < 1e-9), metrics[0])
    if all(row["mean_net_bps"] > 15 for row in metrics) and by_060["t_stat"] > 2:
        return "GREEN"
    if best_mean > 0 and (best_mean <= 15 or 1 <= by_060["t_stat"] <= 2):
        return "AMBER"
    return "RED"


def build_markdown_report(samples: pd.DataFrame, holdout: pd.DataFrame, class_balance: dict[str, Any]) -> str:
    from sklearn.metrics import brier_score_loss, confusion_matrix

    thresholds = [0.55, 0.60, 0.65]
    metrics = [economic_metrics(holdout, t) for t in thresholds]
    verdict = classify_verdict(metrics)
    auc_value = auc_score(holdout["label"], holdout["predicted_probability"]) if not holdout.empty else 0.5
    brier = float(brier_score_loss(holdout["label"], holdout["predicted_probability"])) if not holdout.empty and holdout["label"].nunique() > 0 else 0.0
    cm = confusion_matrix(holdout["label"], holdout["predicted_probability"].ge(0.60).astype(int), labels=[0, 1]) if not holdout.empty else np.zeros((2, 2), dtype=int)
    calibration = calibration_table(holdout)

    lines = ["# Same-Day Momentum Edge Validation", ""]
    lines += ["## Section 1 - Universe and sample", ""]
    symbols = samples["symbol"].nunique() if "symbol" in samples.columns else 0
    lines += [
        f"- Universe size at start of window: {symbols}",
        f"- Universe size at end of window: {symbols}",
        f"- Candidate signals long: {int((samples.get('direction') == 'long').sum()) if not samples.empty else 0}",
        f"- Candidate signals short: {int((samples.get('direction') == 'short').sum()) if not samples.empty else 0}",
        f"- Class balance before downsampling: {class_balance.get('pre_downsample', {})}",
        f"- Class balance after downsampling: {class_balance.get('post_downsample', {})}",
        f"- Number of training samples: {max(0, len(samples) - len(holdout))}",
        f"- Number of holdout samples: {len(holdout)}",
        "",
    ]
    lines += ["## Section 2 - Label distribution", ""]
    lines.append(f"- Continuation rate overall: {float(samples['label'].mean()) if not samples.empty else 0:.4f}")
    if not samples.empty:
        lines.append("- Continuation rate by time-of-day bucket:")
        for key, val in samples.groupby("time_of_day_bucket")["label"].mean().to_dict().items():
            lines.append(f"  - {key}: {val:.4f}")
        lines.append("- Continuation rate by side:")
        for key, val in samples.groupby("direction")["label"].mean().to_dict().items():
            lines.append(f"  - {key}: {val:.4f}")
        lines.append("- Continuation rate by liquidity tier:")
        for key, val in samples.groupby("liquidity_tier")["label"].mean().to_dict().items():
            lines.append(f"  - {key}: {val:.4f}")
    lines.append("")
    lines += ["## Section 3 - Model performance on holdout", "", f"- AUC: {auc_value:.4f}", f"- Brier score: {brier:.4f}", ""]
    lines += ["### Calibration table", "", markdown_table(calibration) if not calibration.empty else "No calibration rows.", ""]
    lines += ["### Confusion matrix at threshold 0.60", "", f"- TN: {int(cm[0,0])}", f"- FP: {int(cm[0,1])}", f"- FN: {int(cm[1,0])}", f"- TP: {int(cm[1,1])}", ""]
    lines += ["## Section 4 - Economic performance on holdout", ""]
    lines.append(markdown_table(pd.DataFrame(metrics)))
    lines.append("")
    lines += ["## Section 5 - Slice analysis", ""]
    for column in ["time_of_day_bucket", "direction", "liquidity_tier"]:
        lines.append(f"### {column}")
        if holdout.empty:
            lines.append("No holdout rows.")
        else:
            rows = []
            for key, group in holdout.groupby(column):
                row = economic_metrics(group, 0.60)
                row[column] = key
                rows.append(row)
            lines.append(markdown_table(pd.DataFrame(rows)) if rows else "No rows.")
        lines.append("")
    lines += ["## Section 6 - Verdict", "", f"**{verdict}**", ""]
    recommendation = {
        "GREEN": "Proceed with SPECs 73-80.",
        "AMBER": "Identify slices with strongest signal; consider narrowing scope before proceeding.",
        "RED": "Do not proceed unless the operator records a continuation decision.",
    }[verdict]
    lines += ["## Section 7 - Recommendations", "", recommendation, ""]
    lines += [
        "## Section 8 - Caveats",
        "",
        "- Survivorship correction depends on the symbols present in the provided historical intraday input.",
        "- Round-trip costs use recorded spread_bps when available plus 5 bps market impact.",
        "- Slippage model is intentionally conservative and fixed for this validation gate.",
        "- Data gaps reduce sample count and are reflected in the sample section.",
        "",
    ]
    return "\n".join(lines)


def run_same_day_edge_measurement(bars: pd.DataFrame, *, report_dir: Path | None = None, stamp: str | None = None) -> Path:
    samples = candidate_signals_from_bars(bars)
    train, holdout_base = split_holdout(samples)
    balanced, class_balance = balance_classes(train)
    holdout = holdout_predictions(balanced, holdout_base) if not balanced.empty and not holdout_base.empty else holdout_base.assign(predicted_probability=0.5)
    report = build_markdown_report(samples, holdout, class_balance)
    output_dir = report_dir or PROJECT_ROOT / "reports" / "same_day_edge"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stamp or timestamp()}.md"
    path.write_text(report, encoding="utf-8")
    return path
