from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
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

SENTIMENT_STORE_FILE = PROCESSED_DIR / "05_news_sentiment_store.csv"


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


def _as_date(value: date | str | None, default: date) -> date:
    if value is None:
        return default
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return parsed.date()


def load_sentiment_store(store_file: Path = SENTIMENT_STORE_FILE) -> pd.DataFrame:
    if not store_file.exists():
        return pd.DataFrame(columns=SENTIMENT_COLUMNS)
    frame = pd.read_csv(store_file, low_memory=False)
    for col in SENTIMENT_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = frame[SENTIMENT_COLUMNS].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    return frame.dropna(subset=["date", "ticker"])


def save_sentiment_store(frame: pd.DataFrame, store_file: Path = SENTIMENT_STORE_FILE) -> None:
    store_file.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    for col in SENTIMENT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[SENTIMENT_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out = out.dropna(subset=["date", "ticker"])
    out = out.sort_values(["ticker", "date"])
    out = out.drop_duplicates(["ticker", "date"], keep="last")
    out.to_csv(store_file, index=False)
    log(f"Updated canonical sentiment store: {store_file} ({len(out):,} rows)")


def determine_sentiment_windows(
    tickers: Iterable[str],
    store: pd.DataFrame,
    *,
    start_date: str = "2018-01-01",
    as_of_date: date | str | None = None,
    force_full: bool = False,
    delta_overlap_days: int = 1,
) -> tuple[dict[str, tuple[date, date]], bool]:
    clean_tickers = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    end_date = _as_date(as_of_date, date.today())
    floor_date = _as_date(start_date, date(2018, 1, 1))
    full_mode = force_full or store.empty
    if full_mode:
        return {ticker: (floor_date, end_date) for ticker in clean_tickers}, True

    working = store.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working["ticker"] = working["ticker"].astype(str).str.upper().str.strip()
    latest_by_ticker = working.groupby("ticker")["date"].max().to_dict()
    overlap = max(1, int(delta_overlap_days or 1))
    windows: dict[str, tuple[date, date]] = {}
    for ticker in clean_tickers:
        latest = latest_by_ticker.get(ticker)
        if latest is None or pd.isna(latest):
            windows[ticker] = (floor_date, end_date)
            continue
        from_date = max(floor_date, pd.Timestamp(latest).date() - timedelta(days=overlap - 1))
        if from_date <= end_date:
            windows[ticker] = (from_date, end_date)
    return windows, False


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
    lookback_days: int | None = None,
    as_of_date: date | str | None = None,
    ticker_windows: dict[str, tuple[date, date]] | None = None,
) -> Dict[str, pd.DataFrame]:
    providers = sentiment_providers_from_name(provider_name)
    clean_tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if limit:
        clean_tickers = clean_tickers[:limit]

    effective_lookback = lookback_days if lookback_days is not None else _env_int("STOCKML_SENTIMENT_LOOKBACK_DAYS", 2)
    end_date = _as_date(as_of_date, date.today())
    start_date = end_date - timedelta(days=max(0, effective_lookback - 1))
    if ticker_windows:
        starts = [window[0] for window in ticker_windows.values()]
        ends = [window[1] for window in ticker_windows.values()]
        start_date = min(starts) if starts else start_date
        end_date = max(ends) if ends else end_date
    total = len(clean_tickers)
    provider_names = ",".join(provider.source_name for provider in providers) or "none"
    progress_every = _env_int("STOCKML_SENTIMENT_PROGRESS_EVERY", 50)
    slow_after_sec = _env_int("STOCKML_SENTIMENT_SLOW_SECONDS", 15)
    log(
        "Building sentiment panel: "
        f"tickers={total:,} providers={provider_names} "
        f"from={start_date} to={end_date} lookback_days={effective_lookback}"
    )

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
        ticker_start, ticker_end = ticker_windows.get(ticker, (start_date, end_date)) if ticker_windows else (start_date, end_date)
        for provider in providers:
            try:
                if hasattr(provider, "fetch_articles_between"):
                    articles = provider.fetch_articles_between(ticker, from_date=ticker_start, to_date=ticker_end)
                else:
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


def build_sentiment_panel(
    limit: Optional[int] = None,
    provider_name: str | None = None,
    lookback_days: int | None = None,
    start_date: str = "2018-01-01",
    force_full: bool = False,
    delta_overlap_days: int = 1,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    universe = pd.read_csv(latest_universe_for_sentiment(), dtype=str)
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in universe.columns else "ticker"
    tickers = universe[ticker_col].dropna().astype(str).str.upper().str.strip()
    if limit:
        tickers = tickers.head(limit)

    store = load_sentiment_store()
    if lookback_days is not None:
        as_of = date.today()
        start = as_of - timedelta(days=max(0, lookback_days - 1))
        windows = {ticker: (start, as_of) for ticker in tickers if ticker}
        full_mode = False
    else:
        windows, full_mode = determine_sentiment_windows(
            tickers,
            store,
            start_date=start_date,
            force_full=force_full,
            delta_overlap_days=delta_overlap_days,
        )
    mode = "full" if full_mode else "delta"
    log(f"Sentiment download mode: {mode}")
    log(f"Tickers in sentiment universe: {len(list(tickers)):,}")
    log(f"Tickers requiring sentiment download: {len(windows):,}")
    result = build_sentiment_panel_for_tickers(
        windows.keys(),
        provider_name=provider_name,
        lookback_days=lookback_days,
        ticker_windows=windows,
    )

    delta_path = PROCESSED_DIR / f"05_news_sentiment_{mode}_{stamp}.csv"
    result["panel"].to_csv(delta_path, index=False)
    log(f"Wrote news sentiment {mode} file: {delta_path} ({len(result['panel']):,} rows)")

    combined = pd.concat([store, result["panel"]], ignore_index=True) if not store.empty else result["panel"]
    save_sentiment_store(combined)

    quality_path = INTERIM_DIR / f"05_news_sentiment_quality_{stamp}.csv"
    result["quality"].to_csv(quality_path, index=False)
    log(f"Wrote news sentiment quality: {quality_path} ({len(result['quality']):,} rows)")
    return {"sentiment_panel": SENTIMENT_STORE_FILE, "sentiment_delta": delta_path, "sentiment_quality": quality_path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--provider", default=None, help="Sentiment provider: eodhd or legacy.")
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--delta-overlap-days", type=int, default=1)
    parser.add_argument("--lookback-days", type=int, default=None, help="Compatibility override for a stateless recent news window.")
    args = parser.parse_args()
    paths = build_sentiment_panel(
        limit=args.limit,
        provider_name=args.provider,
        lookback_days=args.lookback_days,
        start_date=args.start_date,
        force_full=args.force_full,
        delta_overlap_days=args.delta_overlap_days,
    )
    for name, path in paths.items():
        log(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
