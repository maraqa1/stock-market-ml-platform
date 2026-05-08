from __future__ import annotations

import re

POSITIVE_WORDS = {
    "beat", "beats", "bullish", "growth", "upgrade", "strong", "profit", "profits", "surge", "rally", "outperform",
}
NEGATIVE_WORDS = {
    "miss", "misses", "bearish", "downgrade", "weak", "loss", "losses", "drop", "falls", "risk", "underperform",
}


def score_text(text: str) -> float:
    words = re.findall(r"[a-zA-Z]+", str(text).lower())
    if not words:
        return 0.0
    positive = sum(1 for word in words if word in POSITIVE_WORDS)
    negative = sum(1 for word in words if word in NEGATIVE_WORDS)
    return max(-1.0, min(1.0, (positive - negative) / max(1, positive + negative)))


def classify_score(score: float) -> str:
    if score > 0.05:
        return "positive"
    if score < -0.05:
        return "negative"
    return "neutral"

