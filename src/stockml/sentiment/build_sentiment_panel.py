from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, PROCESSED_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.sentiment.cnbc_news_provider import CnbcRssNewsProvider
from stockml.sentiment.sentiment_schema import SENTIMENT_COLUMNS
from stockml.sentiment.simple_sentiment_model import classify_score, score_text
from stockml.sentiment.yahoo_news_provider import YahooNewsProvider


def latest_universe_for_sentiment() -> Path:
    path = latest_file(INTERIM_DIR, "03_us_price_validated_universe_*.csv")
    if path is None:
        path = latest_file(INTERIM_DIR, "02_us_tradable_universe_*.csv")
    if path is None:
        raise FileNotFoundError("No universe file found for sentiment pipeline.")
    return path


def _article_date(article: Dict[str, object]) -> Optional[pd.Timestamp]:
    raw = article.get("providerPublishTime") or article.get("publishTime")
    if raw is None:
        return None
    try:
        return pd.to_datetime(datetime.fromtimestamp(int(raw), tz=timezone.utc).date())
    except Exception:
        return None


def _article_text(article: Dict[str, object]) -> str:
    return " ".join(str(article.get(key, "") or "") for key in ["title", "summary", "publisher"])


def aggregate_articles(ticker: str, articles: List[Dict[str, object]], source: str) -> pd.DataFrame:
    rows = []
    for article in articles:
        article_date = _article_date(article)
        if article_date is None:
            continue
        score = score_text(_article_text(article))
        label = classify_score(score)
        rows.append({"date": article_date, "ticker": ticker, "score": score, "label": label})

    if not rows:
        today = pd.to_datetime(datetime.now().date())
        return pd.DataFrame(
            [{
                "date": today,
                "ticker": ticker,
                "article_count": 0,
                "sentiment_score_mean": pd.NA,
                "sentiment_score_min": pd.NA,
                "sentiment_score_max": pd.NA,
                "sentiment_positive_count": 0,
                "sentiment_negative_count": 0,
                "sentiment_neutral_count": 0,
                "sentiment_source": source,
                "sentiment_status": "no_articles",
            }],
            columns=SENTIMENT_COLUMNS,
        )

    scored = pd.DataFrame(rows)
    grouped = scored.groupby(["date", "ticker"]).agg(
        article_count=("score", "size"),
        sentiment_score_mean=("score", "mean"),
        sentiment_score_min=("score", "min"),
        sentiment_score_max=("score", "max"),
        sentiment_positive_count=("label", lambda s: int((s == "positive").sum())),
        sentiment_negative_count=("label", lambda s: int((s == "negative").sum())),
        sentiment_neutral_count=("label", lambda s: int((s == "neutral").sum())),
    ).reset_index()
    grouped["sentiment_source"] = source
    grouped["sentiment_status"] = "ok"
    return grouped[SENTIMENT_COLUMNS]


def build_sentiment_panel_for_tickers(tickers: Iterable[str], limit: Optional[int] = None) -> Dict[str, pd.DataFrame]:
    providers = [YahooNewsProvider(), CnbcRssNewsProvider()]
    clean_tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if limit:
        clean_tickers = clean_tickers[:limit]

    panels = []
    quality_rows = []
    for ticker in clean_tickers:
        ticker_panels = []
        errors = []
        for provider in providers:
            try:
                articles = provider.fetch_articles(ticker)
                ticker_panels.append(aggregate_articles(ticker, articles, provider.source_name))
            except Exception as exc:
                errors.append(f"{provider.source_name}: {str(exc)[:250]}")

        panel = _combine_provider_panels(ticker, ticker_panels, errors)
        status = "ok" if not panel.empty and panel["article_count"].sum() > 0 else "no_articles"
        if errors and status == "no_articles":
            status = "provider_error"
        panels.append(panel)
        quality_rows.append({"ticker": ticker, "sentiment_status": status, "sentiment_error": " | ".join(errors)})

    return {
        "panel": pd.concat(panels, ignore_index=True) if panels else pd.DataFrame(columns=SENTIMENT_COLUMNS),
        "quality": pd.DataFrame(quality_rows),
    }


def _combine_provider_panels(ticker: str, provider_panels: List[pd.DataFrame], errors: List[str]) -> pd.DataFrame:
    usable = [panel for panel in provider_panels if panel is not None and not panel.empty and panel["article_count"].sum() > 0]
    if not usable:
        source = "none" if not provider_panels else "+".join(panel["sentiment_source"].iloc[0] for panel in provider_panels if not panel.empty)
        return pd.DataFrame(
            [{
                "date": pd.to_datetime(datetime.now().date()),
                "ticker": ticker,
                "article_count": 0,
                "sentiment_score_mean": pd.NA,
                "sentiment_score_min": pd.NA,
                "sentiment_score_max": pd.NA,
                "sentiment_positive_count": 0,
                "sentiment_negative_count": 0,
                "sentiment_neutral_count": 0,
                "sentiment_source": source,
                "sentiment_status": "provider_error" if errors else "no_articles",
            }],
            columns=SENTIMENT_COLUMNS,
        )

    combined = pd.concat(usable, ignore_index=True)
    weighted_score = combined["sentiment_score_mean"] * combined["article_count"]
    out = combined.groupby(["date", "ticker"], as_index=False).agg(
        article_count=("article_count", "sum"),
        sentiment_score_min=("sentiment_score_min", "min"),
        sentiment_score_max=("sentiment_score_max", "max"),
        sentiment_positive_count=("sentiment_positive_count", "sum"),
        sentiment_negative_count=("sentiment_negative_count", "sum"),
        sentiment_neutral_count=("sentiment_neutral_count", "sum"),
    )
    score_by_date = weighted_score.groupby([combined["date"], combined["ticker"]]).sum().reset_index(name="weighted_score")
    out = out.merge(score_by_date, on=["date", "ticker"], how="left")
    out["sentiment_score_mean"] = out["weighted_score"] / out["article_count"].replace(0, pd.NA)
    out["sentiment_source"] = "+".join(sorted(combined["sentiment_source"].dropna().astype(str).unique()))
    out["sentiment_status"] = "ok"
    return out[SENTIMENT_COLUMNS]


def build_sentiment_panel(limit: Optional[int] = None) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    universe = pd.read_csv(latest_universe_for_sentiment(), dtype=str)
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in universe.columns else "ticker"
    result = build_sentiment_panel_for_tickers(universe[ticker_col], limit=limit)

    panel_path = PROCESSED_DIR / f"05_news_sentiment_panel_{stamp}.csv"
    result["panel"].to_csv(panel_path, index=False)
    log(f"Wrote news sentiment panel: {panel_path} ({len(result['panel']):,} rows)")

    quality_path = INTERIM_DIR / f"05_news_sentiment_quality_{stamp}.csv"
    result["quality"].to_csv(quality_path, index=False)
    log(f"Wrote news sentiment quality: {quality_path} ({len(result['quality']):,} rows)")
    return {"sentiment_panel": panel_path, "sentiment_quality": quality_path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    paths = build_sentiment_panel(limit=args.limit)
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
