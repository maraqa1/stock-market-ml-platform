from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
from typing import Protocol
from urllib import request
from urllib.error import URLError
import json

from stockml.common.paths import PROJECT_ROOT


def _timestamp_key(path: Path) -> str:
    match = re.search(r"(\d{8}_\d{6})", path.name)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class AdapterEnrichmentResult:
    status: str
    enriched_file: Path | None = None
    reason: str = ""
    adapter_version: str = ""
    provider: str = "ai2"


class CandidateEnrichmentAdapter(Protocol):
    adapter_version: str
    provider: str

    def enrich(self, raw_candidate_file: Path, *, output_dir: Path, run_id: str) -> AdapterEnrichmentResult:
        ...


class ExistingFileCandidateEnrichmentAdapter:
    """Adapter for workflows where an external enrichment service writes a shortlist.

    This deliberately does not fabricate an enriched file. If a repository-side AI2
    API, Claude workflow, or ChatGPT workflow is added later, it should implement
    the same adapter protocol.
    """

    adapter_version = "existing_file_v1"

    def __init__(self, *, search_root: Path | str | None = None, provider: str = "ai2"):
        self.search_root = Path(search_root) if search_root is not None else PROJECT_ROOT
        self.provider = str(provider or "ai2").strip().lower() or "ai2"

    def enrich(self, raw_candidate_file: Path, *, output_dir: Path, run_id: str) -> AdapterEnrichmentResult:
        candidates = self._candidate_outputs(raw_candidate_file, output_dir=output_dir)
        if not candidates:
            return AdapterEnrichmentResult(
                status="failed",
                reason="enrichment_mechanism_missing_or_output_not_found",
                adapter_version=self.adapter_version,
                provider=self.provider,
            )
        return AdapterEnrichmentResult(
            status="ok",
            enriched_file=max(candidates, key=lambda path: (path.stat().st_mtime, _timestamp_key(path), path.name)),
            adapter_version=self.adapter_version,
            provider=self.provider,
        )

    def _candidate_outputs(self, raw_candidate_file: Path, *, output_dir: Path) -> list[Path]:
        raw_stem = raw_candidate_file.stem
        search_dirs = [
            raw_candidate_file.parent,
            output_dir,
            self.search_root / "data" / "ai2",
            self.search_root / "data" / "portal_outputs",
        ]
        patterns = [
            f"{raw_stem}.shortlist.csv",
            f"{raw_stem}*.shortlist.csv",
            f"{self.provider}_candidate_input_*.shortlist.csv",
            f"{self.provider}_enriched_candidates_*.csv",
            "ai2_candidate_input_*.shortlist.csv",
            "ai2_enriched_candidates_*.csv",
        ]
        found: list[Path] = []
        for directory in search_dirs:
            if not directory.exists():
                continue
            for pattern in patterns:
                found.extend(path for path in directory.glob(pattern) if path.is_file())
        raw_resolved = raw_candidate_file.resolve()
        return sorted({path.resolve() for path in found if path.resolve() != raw_resolved})


class HttpCandidateEnrichmentAdapter:
    """HTTP client for server-side candidate enrichment.

    The API key stays in the environment/config and is sent only as a server-side
    header. The adapter accepts either a CSV response body or a JSON response
    containing one of: ``csv``, ``content``, ``shortlist_csv``, ``file_path`` or
    ``download_url``.
    """

    adapter_version = "http_enrichment_v1"

    def __init__(
        self,
        *,
        endpoint_url: str,
        provider: str = "ai2",
        api_key: str = "",
        timeout_seconds: float = 120.0,
        auth_header: str = "Authorization",
        extra_headers: dict[str, str] | None = None,
    ):
        self.provider = str(provider or "ai2").strip().lower() or "ai2"
        self.endpoint_url = str(endpoint_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.auth_header = str(auth_header or "Authorization").strip() or "Authorization"
        self.extra_headers = dict(extra_headers or {})

    def enrich(self, raw_candidate_file: Path, *, output_dir: Path, run_id: str) -> AdapterEnrichmentResult:
        if not self.endpoint_url:
            return AdapterEnrichmentResult("failed", reason="enrichment_endpoint_missing", adapter_version=self.adapter_version, provider=self.provider)
        if not raw_candidate_file.exists():
            return AdapterEnrichmentResult("failed", reason="raw_candidate_file_missing", adapter_version=self.adapter_version, provider=self.provider)
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            response_body, content_type = self._post_file(raw_candidate_file)
            enriched = self._persist_response(response_body, content_type, output_dir=output_dir, run_id=run_id)
        except Exception as exc:
            return AdapterEnrichmentResult("failed", reason=f"enrichment_http_error:{exc}", adapter_version=self.adapter_version, provider=self.provider)
        return AdapterEnrichmentResult("ok", enriched_file=enriched, adapter_version=self.adapter_version, provider=self.provider)

    def _headers(self, boundary: str) -> dict[str, str]:
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "text/csv, application/json",
            **self.extra_headers,
        }
        if self.api_key:
            value = self.api_key if self.auth_header.lower() != "authorization" else f"Bearer {self.api_key}"
            headers[self.auth_header] = value
        return headers

    def _post_file(self, raw_candidate_file: Path) -> tuple[bytes, str]:
        boundary = "----stockml-ai2-boundary"
        file_bytes = raw_candidate_file.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{raw_candidate_file.name}"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = request.Request(self.endpoint_url, data=body, headers=self._headers(boundary), method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except URLError as exc:
            raise RuntimeError(exc) from exc

    def _persist_response(self, body: bytes, content_type: str, *, output_dir: Path, run_id: str) -> Path:
        text = body.decode("utf-8-sig", errors="replace")
        if "json" in str(content_type).lower() or text.lstrip().startswith("{"):
            payload = json.loads(text)
            for key in ("csv", "content", "shortlist_csv"):
                if payload.get(key):
                    return self._write_csv_text(str(payload[key]), output_dir=output_dir, run_id=run_id)
            file_path = payload.get("file_path") or payload.get("path")
            if file_path:
                source = Path(str(file_path))
                if source.exists():
                    target = output_dir / f"ai2_candidate_input_{run_id}.shortlist.csv"
                    shutil.copy2(source, target)
                    return target
                raise RuntimeError("ai2_response_file_path_not_found")
            download_url = payload.get("download_url") or payload.get("url")
            if download_url:
                return self._download_csv(str(download_url), output_dir=output_dir, run_id=run_id)
            raise RuntimeError("ai2_json_response_missing_csv")
        return self._write_csv_text(text, output_dir=output_dir, run_id=run_id)

    def _write_csv_text(self, text: str, *, output_dir: Path, run_id: str) -> Path:
        target = output_dir / f"{self.provider}_candidate_input_{run_id}.shortlist.csv"
        target.write_text(text, encoding="utf-8")
        return target

    def _download_csv(self, url: str, *, output_dir: Path, run_id: str) -> Path:
        req = request.Request(url, headers={"Accept": "text/csv"})
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:
            body = resp.read()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(body)
            temp = Path(handle.name)
        target = output_dir / f"{self.provider}_candidate_input_{run_id}.shortlist.csv"
        shutil.move(str(temp), target)
        return target


AI2EnrichmentAdapter = CandidateEnrichmentAdapter
ExistingFileAI2EnrichmentAdapter = ExistingFileCandidateEnrichmentAdapter


class AI2HttpEnrichmentAdapter(HttpCandidateEnrichmentAdapter):
    adapter_version = "ai2_http_v1"

    def __init__(self, **kwargs):
        super().__init__(provider="ai2", **kwargs)


def build_candidate_enrichment_adapter(
    *,
    provider: str = "ai2",
    endpoint_url: str = "",
    api_key: str = "",
    timeout_seconds: float = 120.0,
    auth_header: str = "Authorization",
    search_root: Path | str | None = None,
) -> CandidateEnrichmentAdapter:
    provider_name = str(provider or "ai2").strip().lower() or "ai2"
    if str(endpoint_url or "").strip():
        adapter_cls = AI2HttpEnrichmentAdapter if provider_name == "ai2" else HttpCandidateEnrichmentAdapter
        return adapter_cls(
            endpoint_url=endpoint_url,
            **({} if provider_name == "ai2" else {"provider": provider_name}),
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            auth_header=auth_header,
        )
    return ExistingFileCandidateEnrichmentAdapter(search_root=search_root, provider=provider_name)


def build_ai2_enrichment_adapter(
    *,
    endpoint_url: str = "",
    api_key: str = "",
    timeout_seconds: float = 120.0,
    auth_header: str = "Authorization",
    search_root: Path | str | None = None,
) -> AI2EnrichmentAdapter:
    return build_candidate_enrichment_adapter(
        provider="ai2",
        endpoint_url=endpoint_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        auth_header=auth_header,
        search_root=search_root,
    )
