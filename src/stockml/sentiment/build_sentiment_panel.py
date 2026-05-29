from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Dict, Iterable, List, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import INTERIM_DIR, PROCESSED_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.sentiment.provider_factory import sentiment_providers_from_name
from stockml.sentiment.sentiment_schema import SENTIMENT_COLUMNS
from stockml.sentiment.simple_sentiment_model import classify_score, score_text


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name, "")).strip() or default))
    except Exception:
        return default


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
        if isinstance(raw, (int, float)) or str(raw).strip().isdigit():
            return pd.to_datetime(datetime.fromtimestamp(int(raw), tz=timezone.utc).date())
        parsed = pd.to_datetime(raw, errors="coerce", utc=True)
        if pd.isna(parsed):
            return None
        return pd.to_datetime(parsed.date())
    except Exception:
        return None


def _article_text(article: Dict[str, object]) -> str:
    content = article.get("content") if isinstance(article.get("content"), dict) else {}
    nested = " ".join(str(content.get(key, "") or "") for key in ["title", "summary", "description"])
    return " ".join(str(article.get(key, "") or "") for key in ["title", "summary", "publisher"]) + " " + nested


def _article_score(article: Dict[str, object]) -> float:
    provider_score = article.get("providerSentiment")
    try:
        if provider_score not in (None, ""):
            return max(-1.0, min(1.0, float(provider_score)))
    except Exception:
        pass
    return score_text(_article_text(article))


def aggregate_articles(ticker: str, articles: List[Dict[str, object]], source: str) -> pd.DataFrame:
    rows = []
    for article in articles:
        article_date = _article_date(article)
        if article_date is None:
            continue
        score = _article_score(article)
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


def build_sentiment_panel_for_tickers(
    tickers: Iterable[str],
    limit: Optional[int] = None,
    provider_name: str | None = None,
) -> Dict[str, pd.DataFrame]:
    providers = sentiment_providers_from_name(provider_name)
    clean_tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if limit:
        clean_tickers = clean_tickers[:limit]

    total = len(clean_tickers)
    provider_names = ",".join(provider.source_name for provider in providers) or "none"
    progress_every = _env_int("STOCKML_SENTIMENT_PROGRESS_EVERY", 50)
    slow_after_sec = _env_int("STOCKML_SENTIMENT_SLOW_SECONDS", 15)
    log(f"Building sentiment panel: tickers={total:,} providers={provider_names}")

    panels = []
    quality_rows = []
    ok_count = 0
    no_articles_count = 0
    provider_error_count = 0
    total_articles = 0
    started = time.perf_counter()
    for index, ticker in enumerate(clean_tickers, start=1):
        ticker_started = time.perf_counter()
        ticker_panels = []
        errors = []
        provider_counts = {}
        for provider in providers:
            try:
                articles = provider.fetch_articles(ticker)
                provider_counts[provider.source_name] = len(articles)
                ticker_panels.append(aggregate_articles(ticker, articles, provider.source_name))
            except Exception as exc:
                provider_counts[provider.source_name] = 0
                errors.append(f"{provider.source_name}: {str(exc)[:250]}")

        panel = _combine_provider_panels(ticker, ticker_panels, errors)
        status = "ok" if not panel.empty and panel["article_count"].sum() > 0 else "no_articles"
        if errors and status == "no_articles":
            status = "provider_error"
        articles_for_ticker = int(panel["article_count"].sum()) if not panel.empty and "article_count" in panel.columns else 0
        total_articles += articles_for_ticker
        if status == "ok":
            ok_count += 1
        elif status == "provider_error":
            provider_error_count += 1
        else:
            no_articles_count += 1
        panels.append(panel)
        quality_rows.append(
            {
                "ticker": ticker,
                "sentiment_status": status,
                "sentiment_error": " | ".join(errors),
                "provider_article_counts": "|".join(f"{key}:{value}" for key, value in provider_counts.items()),
            }
        )
        ticker_elapsed = time.perf_counter() - ticker_started
        if ticker_elapsed >= slow_after_sec:
            log(
                "Sentiment slow ticker "
                f"{ticker} elapsed_sec={ticker_elapsed:.1f} status={status} "
                f"articles={articles_for_ticker} errors={len(errors)}"
            )
        if index == 1 or index == total or index % progress_every == 0:
            elapsed = time.perf_counter() - started
            log(
                "Sentiment progress "
                f"{index:,}/{total:,} ok={ok_count:,} no_articles={no_articles_count:,} "
                f"provider_error={provider_error_count:,} articles={total_articles:,} "
                f"elapsed_sec={elapsed:.1f}"
            )

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


def build_sentiment_panel(limit: Optional[int] = None, provider_name: str | None = None) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    universe = pd.read_csv(latest_universe_for_sentiment(), dtype=str)
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in universe.columns else "ticker"
    result = build_sentiment_panel_for_tickers(universe[ticker_col], limit=limit, provider_name=provider_name)

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
    parser.add_argument("--provider", default=None, help="Sentiment provider: eodhd or legacy.")
    args = parser.parse_args()
    paths = build_sentiment_panel(limit=args.limit, provider_name=args.provider)
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
