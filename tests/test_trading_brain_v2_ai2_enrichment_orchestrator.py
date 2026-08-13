from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.trading_brain_v2.enrichment.ai2_enrichment_adapter import (
    AI2HttpEnrichmentAdapter,
    AdapterEnrichmentResult,
    HttpCandidateEnrichmentAdapter,
    build_ai2_enrichment_adapter,
    build_candidate_enrichment_adapter,
)
from stockml.trading_brain_v2.enrichment.ai2_enrichment_orchestrator import AI2EnrichmentOrchestrator
from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.safety import assert_v2_live_execution_allowed


class FakeAdapter:
    adapter_version = "fake_ai2_v1"

    def __init__(self, result: AdapterEnrichmentResult | None = None, *, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[tuple[Path, Path, str]] = []

    def enrich(self, raw_candidate_file: Path, *, output_dir: Path, run_id: str) -> AdapterEnrichmentResult:
        self.calls.append((raw_candidate_file, output_dir, run_id))
        if self.raises:
            raise self.raises
        assert self.result is not None
        return self.result


def _raw_candidate_file(path: Path) -> Path:
    raw = path / "execution_ranked_candidates_20260806_092244.csv"
    pd.DataFrame([{"symbol": "ATRC", "execution_rank": 1}]).to_csv(raw, index=False)
    return raw


def _enriched_shortlist(path: Path, **overrides) -> Path:
    row = {
        "Symbol": "ATRC",
        "Side/action": "LONG",
        "Source rank": 1,
        "Candidate status": "executable",
        "Decision": "Proceed candidate",
        "Approved notional": 250,
        "Latest EOD date/close": "2026-08-06 / 39.49",
        "Checks / notes": "ok: price_checks_clear",
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
    }
    row.update(overrides)
    enriched = path / "ai2_candidate_input_20260806_092244.shortlist.csv"
    pd.DataFrame([row]).to_csv(enriched, index=False)
    return enriched


def _config(output_dir: Path) -> TradingBrainConfig:
    return TradingBrainConfig(
        active_version="v2",
        v2_shadow_mode=False,
        v2_allow_live_execution=False,
        v2_paper_execution=True,
        ai2_enrichment_enabled=True,
        ai2_enrichment_output_dir=str(output_dir),
    )


def test_raw_candidate_file_exists_orchestrator_persists_enriched_shortlist(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = _enriched_shortlist(tmp_path)
    output_dir = tmp_path / "data" / "ai2"
    adapter = FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched, adapter_version="fake_ai2_v1"))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(output_dir), root=tmp_path).enrich_and_intake(raw, run_id="run-1")

    assert result.ok
    assert result.canonical_enriched_file == output_dir / "ai2_enriched_candidates_run-1.csv"
    assert result.canonical_enriched_file.exists()
    assert result.row_count == 1
    assert result.intake_status == "ok"
    assert adapter.calls


def test_missing_raw_candidate_file_fails_safe(tmp_path: Path):
    result = AI2EnrichmentOrchestrator(config=_config(tmp_path / "out"), root=tmp_path).enrich_and_intake(tmp_path / "missing.csv", run_id="run-1")

    assert result.fail_safe
    assert result.reason == "raw_candidate_file_missing"


def test_ai2_adapter_failure_fails_safe(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    adapter = FakeAdapter(raises=RuntimeError("provider unavailable"))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(tmp_path / "out"), root=tmp_path).enrich_and_intake(raw, run_id="run-1")

    assert result.fail_safe
    assert result.reason == "ai2_adapter_error:provider unavailable"


def test_empty_enriched_shortlist_fails_safe(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = tmp_path / "empty.shortlist.csv"
    pd.DataFrame(columns=["Decision", "Symbol"]).to_csv(enriched, index=False)
    adapter = FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(tmp_path / "out"), root=tmp_path).enrich_and_intake(raw, run_id="run-1")

    assert result.fail_safe
    assert result.reason == "enriched_candidate_file_empty"


def test_enriched_shortlist_missing_ai2_status_field_fails_safe(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = tmp_path / "missing_status.shortlist.csv"
    pd.DataFrame([{"Symbol": "ATRC", "Candidate status": "executable"}]).to_csv(enriched, index=False)
    adapter = FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(tmp_path / "out"), root=tmp_path).enrich_and_intake(raw, run_id="run-1")

    assert result.fail_safe
    assert result.reason == "ai2_status_field_missing"


def test_successful_enriched_shortlist_is_passed_into_ap_b01_intake(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = _enriched_shortlist(tmp_path)
    adapter = FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(tmp_path / "out"), root=tmp_path).enrich_and_intake(raw, run_id="intake-run")

    assert result.ok
    assert result.intake_status == "ok"
    assert result.canonical_enriched_file is not None
    loaded = pd.read_csv(result.canonical_enriched_file)
    assert loaded.iloc[0]["Decision"] == "Proceed candidate"


def test_real_ai2_execution_decision_column_is_accepted(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = _enriched_shortlist(tmp_path)
    frame = pd.read_csv(enriched)
    frame = frame.rename(columns={"Decision": "execution_decision"})
    frame.to_csv(enriched, index=False)
    adapter = FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(tmp_path / "out"), root=tmp_path).enrich_and_intake(raw, run_id="real-ai2")

    assert result.ok
    assert result.canonical_enriched_file is not None
    loaded = pd.read_csv(result.canonical_enriched_file)
    assert loaded.iloc[0]["execution_decision"] == "Proceed candidate"


def test_audit_events_are_created_for_enrichment_start_and_completion(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = _enriched_shortlist(tmp_path)
    audit_path = tmp_path / "audit" / "events.jsonl"
    adapter = FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched))

    result = AI2EnrichmentOrchestrator(adapter=adapter, config=_config(tmp_path / "out"), audit_path=audit_path, root=tmp_path).enrich_and_intake(raw, run_id="audit-run")

    event_types = [event.event_type for event in result.audit_events]
    assert "ai2_enrichment_started" in event_types
    assert "ai2_enrichment_completed" in event_types
    assert "ai2_enriched_shortlist_handed_to_v2_intake" in event_types
    assert audit_path.exists()


def test_no_live_execution_occurs_or_is_allowed(tmp_path: Path):
    raw = _raw_candidate_file(tmp_path)
    enriched = _enriched_shortlist(tmp_path)
    cfg = _config(tmp_path / "out")

    result = AI2EnrichmentOrchestrator(adapter=FakeAdapter(AdapterEnrichmentResult("ok", enriched_file=enriched)), config=cfg, root=tmp_path).enrich_and_intake(raw, run_id="safe-run")

    assert result.ok
    assert cfg.v2_allow_live_execution is False
    try:
        assert_v2_live_execution_allowed(requested_live_execution=True, config=cfg)
    except RuntimeError as exc:
        assert str(exc) == "trading_brain_v2_live_execution_disabled"
    else:
        raise AssertionError("V2 live execution guard should remain closed")


def test_http_adapter_persists_csv_response(tmp_path: Path, monkeypatch):
    raw = _raw_candidate_file(tmp_path)

    class FakeResponse:
        headers = {"Content-Type": "text/csv"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"Symbol,Decision\nATRC,Proceed candidate\n"

    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("stockml.trading_brain_v2.enrichment.ai2_enrichment_adapter.request.urlopen", fake_urlopen)

    result = AI2HttpEnrichmentAdapter(endpoint_url="https://ai2.example/enrich", api_key="secret", timeout_seconds=5).enrich(raw, output_dir=tmp_path / "out", run_id="run-1")

    assert result.status == "ok"
    assert result.enriched_file is not None
    assert result.enriched_file.read_text(encoding="utf-8") == "Symbol,Decision\nATRC,Proceed candidate\n"
    assert captured == {"url": "https://ai2.example/enrich", "auth": "Bearer secret", "timeout": 5.0}


def test_http_adapter_persists_json_csv_response(tmp_path: Path, monkeypatch):
    raw = _raw_candidate_file(tmp_path)

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"csv":"Symbol,Decision\\nGCT,Proceed candidate\\n"}'

    monkeypatch.setattr("stockml.trading_brain_v2.enrichment.ai2_enrichment_adapter.request.urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = AI2HttpEnrichmentAdapter(endpoint_url="https://ai2.example/enrich").enrich(raw, output_dir=tmp_path / "out", run_id="run-json")

    assert result.status == "ok"
    assert result.enriched_file is not None
    assert "GCT" in result.enriched_file.read_text(encoding="utf-8")


def test_http_adapter_persists_ai2_files_enriched_csv_response(tmp_path: Path, monkeypatch):
    raw = _raw_candidate_file(tmp_path)

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"status":"partial_ok","files":{"enriched_csv":'
                b'{"content":"symbol,execution_decision,ai2_realtime_price\\nATRC,Proceed candidate,40.12\\n"}}}'
            )

    monkeypatch.setattr("stockml.trading_brain_v2.enrichment.ai2_enrichment_adapter.request.urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = AI2HttpEnrichmentAdapter(endpoint_url="https://ai2.example/enrich").enrich(raw, output_dir=tmp_path / "out", run_id="run-ai2-files")

    assert result.status == "ok"
    assert result.enriched_file is not None
    loaded = pd.read_csv(result.enriched_file)
    assert loaded.loc[0, "symbol"] == "ATRC"
    assert loaded.loc[0, "ai2_realtime_price"] == 40.12


def test_http_adapter_persists_ai2_json_rows_response(tmp_path: Path, monkeypatch):
    raw = _raw_candidate_file(tmp_path)

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"status":"partial_ok","rows":['
                b'{"symbol":"ATRC","execution_decision":"Proceed candidate","ai2_realtime_price":40.12},'
                b'{"symbol":"GCT","execution_decision":"Proceed candidate","ai2_realtime_price":54.10}'
                b"]}"
            )

    monkeypatch.setattr("stockml.trading_brain_v2.enrichment.ai2_enrichment_adapter.request.urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = AI2HttpEnrichmentAdapter(endpoint_url="https://ai2.example/enrich").enrich(raw, output_dir=tmp_path / "out", run_id="run-ai2-rows")

    assert result.status == "ok"
    assert result.enriched_file is not None
    loaded = pd.read_csv(result.enriched_file)
    assert list(loaded["symbol"]) == ["ATRC", "GCT"]
    assert list(loaded["ai2_realtime_price"]) == [40.12, 54.10]


def test_adapter_factory_uses_http_when_endpoint_configured():
    assert isinstance(build_ai2_enrichment_adapter(endpoint_url="https://ai2.example/enrich"), AI2HttpEnrichmentAdapter)


def test_generic_adapter_factory_supports_future_providers():
    adapter = build_candidate_enrichment_adapter(provider="chatgpt", endpoint_url="https://agent.example/enrich")

    assert isinstance(adapter, HttpCandidateEnrichmentAdapter)
    assert adapter.provider == "chatgpt"


def test_provider_specific_config_reads_endpoint_and_key_env(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "autopilot.yaml"
    config_path.write_text(
        """
trading_brain:
  ai2_enrichment:
    enabled: true
    provider: claude
    output_dir: data/ai2
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_ENRICHMENT_ENDPOINT", "https://claude.example/enrich")
    monkeypatch.setenv("CLAUDE_API_KEY", "secret-value")

    cfg = load_trading_brain_config(config_path)

    assert cfg.ai2_enrichment_provider == "claude"
    assert cfg.ai2_enrichment_endpoint_url == "https://claude.example/enrich"
    assert cfg.ai2_enrichment_api_key_env == "CLAUDE_API_KEY"
    assert cfg.ai2_enrichment_api_key == "secret-value"


def test_config_reads_standalone_ai2_enrichment_yaml(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    autopilot_path = config_dir / "autopilot.yaml"
    autopilot_path.write_text(
        """
trading_brain:
  active_version: v2
  v2_shadow_mode: true
""",
        encoding="utf-8",
    )
    (config_dir / "ai2_enrichment.yaml").write_text(
        """
ai2_enrichment:
  enabled: true
  provider: ai2
  endpoint_url: https://ai2.example/enrich
  api_key_env: AI2_API_KEY
  timeout_seconds: 45
  output_dir: data/ai2
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI2_API_KEY", "secret-value")

    cfg = load_trading_brain_config(autopilot_path)

    assert cfg.ai2_enrichment_enabled is True
    assert cfg.ai2_enrichment_endpoint_url == "https://ai2.example/enrich"
    assert cfg.ai2_enrichment_api_key_env == "AI2_API_KEY"
    assert cfg.ai2_enrichment_api_key == "secret-value"
    assert cfg.ai2_enrichment_timeout_seconds == 45


def test_standalone_ai2_config_supplies_endpoint_when_autopilot_endpoint_blank(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    autopilot_path = config_dir / "autopilot.yaml"
    autopilot_path.write_text(
        """
trading_brain:
  ai2_enrichment:
    enabled: true
    endpoint_url: ''
    api_key_env: AI2_API_KEY
""",
        encoding="utf-8",
    )
    (config_dir / "ai2_enrichment.yaml").write_text(
        """
ai2_enrichment:
  endpoint_url: https://ai2.example/enrich
  timeout_seconds: 45
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI2_API_KEY", "secret-value")

    cfg = load_trading_brain_config(autopilot_path)

    assert cfg.ai2_enrichment_endpoint_url == "https://ai2.example/enrich"
    assert cfg.ai2_enrichment_api_key == "secret-value"
    assert cfg.ai2_enrichment_timeout_seconds == 45
