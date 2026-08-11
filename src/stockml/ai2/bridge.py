from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from typing import Any, Callable
from urllib import request
from urllib.error import HTTPError, URLError

import pandas as pd

from stockml.ai2.candidate_enrichment import (
    Ai2EnrichmentConfig,
    apply_ai2_enrichment,
    load_ai2_enrichment_config,
    latest_ai2_merged_candidates_path,
    normalize_ai2_enrichment,
    write_ai2_enriched_candidates,
)
from stockml.ai2.candidate_input import build_ai2_candidate_input, write_ai2_candidate_input
from stockml.candidates.execution_ranker import latest_candidate_or_plan, latest_execution_ranked_path, write_execution_ranked_candidates
from stockml.common.paths import data_root, timestamp
from stockml.trading.ticker_direction_memory import apply_ticker_direction_memory, load_latest_ticker_direction_memory


Transport = Callable[[str, bytes, dict[str, str], int], tuple[bytes, dict[str, str]]]


@dataclass(frozen=True)
class Ai2BridgeResult:
    status: str
    candidate_path: str = ""
    input_path: str = ""
    response_path: str = ""
    merged_path: str = ""
    manifest_path: str = ""
    rows: int = 0
    ai2_rows: int = 0
    ai2_auto_open_allowed: int = 0
    message: str = ""


def ai2_enrichment_refresh_needed(
    *,
    root: Path | str | None = None,
    config: Ai2EnrichmentConfig | None = None,
) -> bool:
    cfg = config or load_ai2_enrichment_config()
    if not cfg.enabled or not cfg.auto_refresh_before_autopilot_tick:
        return False
    source_path, _ = latest_candidate_or_plan(root)
    ranked_path = latest_execution_ranked_path(root)
    ai2_path = latest_ai2_merged_candidates_path(root)
    if ai2_path is None or not ai2_path.exists():
        return True
    ai2_mtime = ai2_path.stat().st_mtime
    if source_path is not None and source_path.exists() and source_path.stat().st_mtime > ai2_mtime:
        return True
    if ranked_path is not None and ranked_path.exists() and ranked_path.stat().st_mtime > ai2_mtime:
        return True
    max_age_seconds = max(int(cfg.max_enrichment_age_minutes or 0), 0) * 60
    if max_age_seconds <= 0:
        return False
    return (datetime.now(timezone.utc).timestamp() - ai2_mtime) > max_age_seconds


def run_ai2_enrichment_bridge(
    *,
    root: Path | str | None = None,
    candidate_file: Path | str | None = None,
    output_dir: Path | str | None = None,
    config: Ai2EnrichmentConfig | None = None,
    transport: Transport | None = None,
    submit: bool = True,
    stamp: str | None = None,
) -> Ai2BridgeResult:
    cfg = config or load_ai2_enrichment_config()
    run_stamp = stamp or timestamp()
    base = data_root(root)
    ai2_dir = Path(output_dir) if output_dir else base / "ai2"
    portal_dir = base / "portal_outputs"
    ai2_dir.mkdir(parents=True, exist_ok=True)
    portal_dir.mkdir(parents=True, exist_ok=True)

    candidate_path = Path(candidate_file) if candidate_file else _ensure_execution_ranked_candidates(root=root, stamp=run_stamp)
    if candidate_path is None or not candidate_path.exists():
        return _write_manifest(
            Ai2BridgeResult(status="missing_data", message="execution_ranked_candidates_missing"),
            ai2_dir=ai2_dir,
            stamp=run_stamp,
            config=cfg,
        )

    candidates = pd.read_csv(candidate_path, low_memory=False)
    input_frame = build_ai2_candidate_input(candidates, limit=cfg.candidate_limit)
    input_path = write_ai2_candidate_input(input_frame, output_dir=ai2_dir, limit=cfg.candidate_limit, stamp=run_stamp)
    base_result = {
        "candidate_path": str(candidate_path),
        "input_path": str(input_path),
        "rows": int(len(input_frame)),
    }

    if not submit or not cfg.api_enabled or not cfg.endpoint_url:
        return _write_manifest(
            Ai2BridgeResult(
                status="api_disabled",
                message="candidate_input_written_api_not_called",
                **base_result,
            ),
            ai2_dir=ai2_dir,
            stamp=run_stamp,
            config=cfg,
        )

    payload = _request_payload(input_frame, input_path=input_path, candidate_path=candidate_path)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/csv"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    try:
        body, response_headers = (transport or _http_transport)(cfg.endpoint_url, payload, headers, cfg.timeout_seconds)
        ai2_frame = _parse_ai2_response(body, response_headers)
    except Exception as exc:
        return _write_manifest(
            Ai2BridgeResult(
                status="api_error",
                message=str(exc),
                **base_result,
            ),
            ai2_dir=ai2_dir,
            stamp=run_stamp,
            config=cfg,
        )

    if ai2_frame.empty or "symbol" not in ai2_frame.columns:
        return _write_manifest(
            Ai2BridgeResult(
                status="invalid_response",
                message="ai2_response_missing_symbol_rows",
                **base_result,
            ),
            ai2_dir=ai2_dir,
            stamp=run_stamp,
            config=cfg,
        )

    response_path = ai2_dir / f"ai2_candidate_response_{run_stamp}.csv"
    ai2_frame.to_csv(response_path, index=False)
    merged = apply_ai2_enrichment(candidates, ai2_frame, config=cfg)
    merged_path = write_ai2_enriched_candidates(candidates, ai2_frame, output_dir=portal_dir, config=cfg, stamp=run_stamp)

    return _write_manifest(
        Ai2BridgeResult(
            status="ok",
            response_path=str(response_path),
            merged_path=str(merged_path),
            ai2_rows=int(len(ai2_frame)),
            ai2_auto_open_allowed=int(merged["ai2_auto_open_allowed"].fillna(False).astype(bool).sum()),
            **base_result,
        ),
        ai2_dir=ai2_dir,
        stamp=run_stamp,
        config=cfg,
    )


def _ensure_execution_ranked_candidates(*, root: Path | str | None = None, stamp: str | None = None) -> Path | None:
    ranked_path = latest_execution_ranked_path(root)
    source_path, candidates = latest_candidate_or_plan(root)
    if source_path is None or candidates.empty:
        return ranked_path
    if ranked_path is not None and ranked_path.exists() and ranked_path.stat().st_mtime >= source_path.stat().st_mtime:
        return ranked_path

    _, memory = load_latest_ticker_direction_memory(root)
    candidates = apply_ticker_direction_memory(candidates, memory)
    return write_execution_ranked_candidates(
        candidates,
        output_dir=data_root(root) / "portal_outputs",
        stamp=stamp,
        source_path=source_path,
    )


def _request_payload(frame: pd.DataFrame, *, input_path: Path, candidate_path: Path) -> bytes:
    payload = {
        "source": "stockml",
        "candidate_path": str(candidate_path),
        "input_path": str(input_path),
        "rows": frame.fillna("").to_dict("records"),
    }
    return json.dumps(payload, allow_nan=False).encode("utf-8")


def _http_transport(url: str, payload: bytes, headers: dict[str, str], timeout_seconds: int) -> tuple[bytes, dict[str, str]]:
    req = request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # nosec B310 - configured internal AI2 endpoint
            return response.read(), {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ai2_http_error:{exc.code}:{detail[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"ai2_connection_error:{exc.reason}") from exc


def _parse_ai2_response(body: bytes, headers: dict[str, str]) -> pd.DataFrame:
    text = body.decode("utf-8-sig", errors="replace").strip()
    content_type = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
    if "json" in content_type or text.startswith("{") or text.startswith("["):
        payload = json.loads(text)
        if isinstance(payload, list):
            return normalize_ai2_enrichment(pd.DataFrame(payload))
        if not isinstance(payload, dict):
            return pd.DataFrame()
        for key in ("rows", "data", "candidates", "shortlist"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return normalize_ai2_enrichment(pd.DataFrame(rows))
        for key in ("csv", "content", "text"):
            csv_text = payload.get(key)
            if isinstance(csv_text, str) and csv_text.strip():
                return normalize_ai2_enrichment(pd.read_csv(StringIO(csv_text)))
        return pd.DataFrame()
    return normalize_ai2_enrichment(pd.read_csv(StringIO(text)))


def _write_manifest(
    result: Ai2BridgeResult,
    *,
    ai2_dir: Path,
    stamp: str,
    config: Ai2EnrichmentConfig,
) -> Ai2BridgeResult:
    manifest_path = ai2_dir / f"ai2_enrichment_bridge_manifest_{stamp}.json"
    safe_config = asdict(config)
    safe_config["api_key"] = "***" if config.api_key else ""
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "result": asdict(result),
        "config": safe_config,
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return Ai2BridgeResult(**{**asdict(result), "manifest_path": str(manifest_path)})
