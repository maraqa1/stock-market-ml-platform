from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from stockml.common.paths import PROJECT_ROOT
from stockml.trading_brain_v2.audit.logger import AuditLogger, build_audit_event
from stockml.trading_brain_v2.autopilot.ap_b01_gold_dataset_intake import GoldDatasetIntakeBlock
from stockml.trading_brain_v2.enrichment.ai2_enrichment_adapter import AI2EnrichmentAdapter, ExistingFileAI2EnrichmentAdapter
from stockml.trading_brain_v2.enrichment.ai2_enrichment_result import AI2EnrichmentResult
from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config


AI2_STATUS_COLUMNS = ("Decision", "ai2_status", "decision", "ai2_decision")


class AI2EnrichmentOrchestrator:
    def __init__(
        self,
        *,
        adapter: AI2EnrichmentAdapter | None = None,
        config: TradingBrainConfig | None = None,
        audit_path: Path | str | None = None,
        root: Path | str | None = None,
    ):
        self.root = Path(root) if root is not None else PROJECT_ROOT
        self.config = config or load_trading_brain_config()
        self.adapter = adapter or ExistingFileAI2EnrichmentAdapter(search_root=self.root)
        self.audit_logger = AuditLogger(audit_path) if audit_path is not None else None

    def enrich_and_intake(self, raw_candidate_file: Path | str, *, run_id: str | None = None) -> AI2EnrichmentResult:
        run = run_id or self._run_id()
        timestamp = self._timestamp()
        raw = Path(raw_candidate_file)
        events: list[Any] = []

        def event(event_type: str, message: str, details: dict[str, Any] | None = None):
            audit = build_audit_event(
                event_type=event_type,
                run_id=run,
                source_file=str(raw),
                symbol="",
                message=message,
                config=self.config,
                details={
                    "raw_candidate_file": str(raw),
                    "timestamp": timestamp,
                    "adapter_version": getattr(self.adapter, "adapter_version", ""),
                    **(details or {}),
                },
            )
            events.append(audit)
            if self.audit_logger is not None:
                self.audit_logger.append(audit)

        event("ai2_raw_candidate_file_received", "raw candidate file received")

        if not raw.exists() or not raw.is_file():
            event("ai2_enrichment_failed", "raw candidate file missing", {"reason": "raw_candidate_file_missing"})
            return self._result("fail_safe", raw, None, None, run, timestamp, events, "raw_candidate_file_missing")
        try:
            with raw.open("rb"):
                pass
        except OSError as exc:
            event("ai2_enrichment_failed", "raw candidate file unreadable", {"reason": str(exc)})
            return self._result("fail_safe", raw, None, None, run, timestamp, events, "raw_candidate_file_unreadable")

        if not self.config.ai2_enrichment_enabled:
            event("ai2_enrichment_failed", "AI2 enrichment disabled", {"reason": "ai2_enrichment_disabled"})
            return self._result("fail_safe", raw, None, None, run, timestamp, events, "ai2_enrichment_disabled")

        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        event("ai2_enrichment_started", "AI2 enrichment started", {"output_dir": str(output_dir)})

        try:
            adapter_result = self.adapter.enrich(raw, output_dir=output_dir, run_id=run)
        except Exception as exc:
            event("ai2_enrichment_failed", "AI2 enrichment adapter failed", {"reason": str(exc)})
            return self._result("fail_safe", raw, None, None, run, timestamp, events, f"ai2_adapter_error:{exc}")

        if adapter_result.status != "ok" or adapter_result.enriched_file is None:
            reason = adapter_result.reason or "ai2_enrichment_failed"
            event("ai2_enrichment_failed", "AI2 enrichment failed", {"reason": reason})
            return self._result("fail_safe", raw, None, None, run, timestamp, events, reason)

        enriched = Path(adapter_result.enriched_file)
        validation_reason, row_count = self._validate_enriched_file(enriched)
        if validation_reason:
            event("ai2_enrichment_failed", "AI2 enriched shortlist invalid", {"reason": validation_reason, "enriched_candidate_file": str(enriched)})
            return self._result("fail_safe", raw, enriched, None, run, timestamp, events, validation_reason, row_count=row_count)

        canonical = self._canonical_output_path(output_dir, run)
        if enriched.resolve() != canonical.resolve():
            shutil.copy2(enriched, canonical)
        event(
            "ai2_enrichment_completed",
            "AI2 enrichment completed",
            {"enriched_candidate_file": str(enriched), "canonical_enriched_file": str(canonical), "row_count": row_count},
        )

        intake = GoldDatasetIntakeBlock().load_candidate_file(path=canonical, root=self.root)
        if intake.status != "ok" or intake.row_count <= 0:
            reason = intake.reason or "v2_intake_failed_after_ai2_enrichment"
            event("ai2_enrichment_failed", "enriched shortlist intake failed", {"reason": reason, "intake_status": intake.status})
            return self._result("fail_safe", raw, enriched, canonical, run, timestamp, events, reason, row_count=row_count, intake_status=intake.status, intake_reason=intake.reason)

        event("ai2_enriched_shortlist_handed_to_v2_intake", "enriched shortlist handed to V2 intake", {"canonical_enriched_file": str(canonical), "intake_rows": intake.row_count})
        return self._result("ok", raw, enriched, canonical, run, timestamp, events, "", row_count=row_count, intake_status=intake.status)

    def _output_dir(self) -> Path:
        configured = Path(self.config.ai2_enrichment_output_dir)
        return configured if configured.is_absolute() else self.root / configured

    def _canonical_output_path(self, output_dir: Path, run_id: str) -> Path:
        return output_dir / f"ai2_enriched_candidates_{run_id}.csv"

    def _validate_enriched_file(self, path: Path) -> tuple[str, int]:
        if not path.exists() or not path.is_file():
            return "enriched_candidate_file_missing", 0
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            return f"enriched_candidate_file_read_error:{exc}", 0
        if frame.empty:
            return "enriched_candidate_file_empty", 0
        if not any(column in frame.columns for column in AI2_STATUS_COLUMNS):
            return "ai2_status_field_missing", len(frame)
        return "", len(frame)

    def _result(
        self,
        status: str,
        raw: Path | None,
        enriched: Path | None,
        canonical: Path | None,
        run_id: str,
        timestamp: str,
        events: list[Any],
        reason: str,
        *,
        row_count: int = 0,
        intake_status: str = "",
        intake_reason: str = "",
    ) -> AI2EnrichmentResult:
        return AI2EnrichmentResult(
            status=status,
            raw_candidate_file=raw,
            enriched_candidate_file=enriched,
            canonical_enriched_file=canonical,
            run_id=run_id,
            timestamp=timestamp,
            adapter_version=getattr(self.adapter, "adapter_version", ""),
            row_count=row_count,
            reason=reason,
            intake_status=intake_status,
            intake_reason=intake_reason,
            audit_events=tuple(events),
        )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _run_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
