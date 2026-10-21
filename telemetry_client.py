import os
import json
import logging
import threading
import requests
from typing import Optional, Dict, Any
from contextvars import ContextVar

OBSERVABILITY_URL = os.getenv("OBSERVABILITY_SERVICE_URL", "http://localhost:8082").rstrip("/")
logger = logging.getLogger("uvicorn")

# Context variables for distributed request and correlation tracking
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id_ctx", default=None)
correlation_id_ctx: ContextVar[Optional[str]] = ContextVar("correlation_id_ctx", default=None)
workspace_id_ctx: ContextVar[Optional[str]] = ContextVar("workspace_id_ctx", default=None)
document_id_ctx: ContextVar[Optional[str]] = ContextVar("document_id_ctx", default=None)
job_id_ctx: ContextVar[Optional[str]] = ContextVar("job_id_ctx", default=None)


def set_context(
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    document_id: Optional[str] = None,
    job_id: Optional[str] = None,
):
    if request_id is not None:
        request_id_ctx.set(request_id)
    if correlation_id is not None:
        correlation_id_ctx.set(correlation_id)
    if workspace_id is not None:
        workspace_id_ctx.set(workspace_id)
    if document_id is not None:
        document_id_ctx.set(document_id)
    if job_id is not None:
        job_id_ctx.set(job_id)


def clear_context():
    request_id_ctx.set(None)
    correlation_id_ctx.set(None)
    workspace_id_ctx.set(None)
    document_id_ctx.set(None)
    job_id_ctx.set(None)


def get_request_id() -> Optional[str]:
    return request_id_ctx.get()


def get_correlation_id() -> Optional[str]:
    return correlation_id_ctx.get()


def get_workspace_id() -> Optional[str]:
    return workspace_id_ctx.get()


class TelemetryClient:
    """
    Non-blocking HTTP client that forwards operational telemetry events
    and structured logs to the centralized Go Observability Microservice.
    """

    def __init__(self, base_url: str = OBSERVABILITY_URL):
        self.base_url = base_url

    def _async_post(self, path: str, payload: Dict[str, Any]):
        """Dispatches telemetry asynchronously to ensure zero latency overhead on main application threads."""
        url = f"{self.base_url}{path}"

        def _send():
            try:
                requests.post(url, json=payload, timeout=1.0)
            except Exception:
                # Fire-and-forget: telemetry unavailability never blocks core business operations
                pass

        threading.Thread(target=_send, daemon=True).start()

    def record_http_request(self, method: str, endpoint: str, status_code: int, duration_ms: float):
        self._async_post("/telemetry/metrics", {
            "type": "http",
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
            "duration_ms": duration_ms,
        })

    def record_ingestion(self, success: bool, duration_ms: float, error_reason: Optional[str] = None):
        self._async_post("/telemetry/metrics", {
            "type": "ingestion",
            "success": success,
            "duration_ms": duration_ms,
            "error_reason": error_reason or "",
        })

    def record_embedding(self, num_chunks: int, duration_ms: float, success: bool = True):
        self._async_post("/telemetry/metrics", {
            "type": "embedding",
            "chunks": num_chunks,
            "duration_ms": duration_ms,
            "success": success,
        })

    def record_retrieval(self, chunks_retrieved: int, duration_ms: float):
        self._async_post("/telemetry/metrics", {
            "type": "retrieval",
            "chunks": chunks_retrieved,
            "duration_ms": duration_ms,
        })

    def record_llm(
        self,
        duration_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        success: bool = True,
        cost_usd: float = 0.0,
    ):
        self._async_post("/telemetry/metrics", {
            "type": "llm",
            "duration_ms": duration_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "success": success,
            "cost_usd": cost_usd,
        })

    def log(
        self,
        level: str,
        message: str,
        service: str = "ai-backend",
        extra: Optional[Dict[str, Any]] = None,
    ):
        payload = {
            "service": service,
            "level": level,
            "message": message,
            "request_id": get_request_id() or "",
            "correlation_id": get_correlation_id() or "",
            "workspace_id": get_workspace_id() or "",
            "document_id": document_id_ctx.get() or "",
            "job_id": job_id_ctx.get() or "",
            "extra": extra or {},
        }
        self._async_post("/telemetry/logs", payload)


telemetry = TelemetryClient()
