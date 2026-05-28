from __future__ import annotations

import pandas as pd

from stockml.same_day.labels import compute_continuation_label


def _bars(decision: pd.Timestamp, decision_high: float = 200) -> pd.DataFrame:
    times = pd.date_range(decision - pd.Timedelta(minutes=30), periods=14, freq="5min", tz="UTC")
    rows = []
    for ts in times:
        rows.append({"timestamp": ts, "open": 100, "high": 100.2, "low": 99.8, "close": 100, "volume": 1000})
    frame = pd.DataFrame(rows)
    frame.loc[frame["timestamp"].eq(decision), "high"] = decision_high
    entry = decision + pd.Timedelta(minutes=5)
    frame.loc[frame["timestamp"].eq(entry), ["open", "high", "low", "close"]] = [100, 100.6, 99.9, 100.4]
    return frame


def test_label_lag_no_lookahead():
    decision = pd.Timestamp("2026-05-28T15:00:00Z")
    first = compute_continuation_label(_bars(decision, decision_high=100.1), decision, "long")
    second = compute_continuation_label(_bars(decision, decision_high=500.0), decision, "long")

    assert first == 1
    assert second == 1


def test_label_returns_none_without_entry_bar():
    decision = pd.Timestamp("2026-05-28T15:00:00Z")
    frame = _bars(decision)
    frame = frame[~frame["timestamp"].eq(decision + pd.Timedelta(minutes=5))]

    assert compute_continuation_label(frame, decision, "long") is None
