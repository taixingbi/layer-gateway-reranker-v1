"""`POST /v1/rerank`: admission queue, routing, upstream proxy, metrics."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import log_gateway_event, new_request_id
from app.metrics.prometheus import (
    ADMISSION_INQUEUE,
    ADMISSION_REJECTED,
    ADMISSION_WAIT,
    BACKEND_SELECTED,
    FAILURES,
    INFLIGHT,
    LATENCY,
    REQUESTS,
    RETRIES,
)
from app.models.schemas import RerankRequest
from app.routing.selector import BackendSelector

logger = logging.getLogger(__name__)
router = APIRouter()
_BLOCKED_HEADERS = frozenset({"host", "content-length"})
_CORRELATION_KEYS_LOWER = frozenset({"x-request-id", "x-trace-id", "x-session-id"})
_CORRELATION_KEYS_BODY = frozenset(
    {
        "x-request-id",
        "x-trace-id",
        "x-session-id",
        "request_id",
        "trace_id",
        "session_id",
    }
)
_RERANK_PATH = "/v1/rerank"


def _routing_debug(selector: BackendSelector, excluded: set[str]) -> dict[str, object]:
    """
    Build `gateway_meta` for routing logs: per-backend score, inflight, latency EWMA,
    error rate, circuit fields, and routing weights.
    """
    r = selector.routing
    rows: dict[str, object] = {}
    for b in selector.backends:
        st = selector.state[b.name]
        err = st.error_rate()
        score = (st.inflight * r.inflight_weight) + (st.latency_ms * r.latency_weight) + (err * r.error_weight)
        circuit_open = st.circuit_open()
        rows[b.name] = {
            "score": score,
            "inflight": st.inflight,
            "latency_ms": st.latency_ms,
            "error_rate": err,
            "requests": st.requests,
            "errors": st.errors,
            "circuit_open": circuit_open,
            "circuit_half_open": st.circuit_half_open,
            "half_open_inflight": st.half_open_inflight,
            "half_open_successes": st.half_open_successes,
            "excluded": b.name in excluded,
            "eligible": (b.name not in excluded) and (not circuit_open),
        }
    return {
        "backends": rows,
        "weights": {
            "inflight": r.inflight_weight,
            "latency": r.latency_weight,
            "error": r.error_weight,
        },
    }


@dataclass
class GatewayContext:
    """Shared process state: settings, selector, HTTP client, admission semaphore."""

    settings: Settings
    selector: BackendSelector
    client: httpx.AsyncClient
    queue: asyncio.Semaphore


def _resolve_correlation_ids(request: Request) -> tuple[str, str, str]:
    """
    Return `X-Request-Id`, `X-Trace-Id`, and `X-Session-Id` for logs and upstream.

    Missing or blank (after strip) values are replaced with a new UUID via `new_request_id`.
    """
    def one(header_key_lower: str) -> str:
        raw = request.headers.get(header_key_lower)
        if raw is None:
            return new_request_id()
        stripped = raw.strip()
        return stripped if stripped else new_request_id()

    return (one("x-request-id"), one("x-trace-id"), one("x-session-id"))


def _build_outbound_headers(
    request: Request,
    request_id: str,
    trace_id: str,
    session_id: str,
) -> dict[str, str]:
    """Hop-by-hop and correlation stripped in one pass; canonical correlation headers set last."""
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _BLOCKED_HEADERS or lk in _CORRELATION_KEYS_LOWER:
            continue
        out[k] = v
    out["X-Request-Id"] = request_id
    out["X-Trace-Id"] = trace_id
    out["X-Session-Id"] = session_id
    return out


def _body_contains_correlation_id(payload: object) -> bool:
    """Reject body-level correlation IDs; they must be sent via headers only."""
    if not isinstance(payload, dict):
        return False
    lowered_keys = {str(k).strip().lower() for k in payload.keys()}
    return any(key in lowered_keys for key in _CORRELATION_KEYS_BODY)


@router.post("/v1/rerank")
async def rerank(request: Request) -> Response:
    """
    Proxy a rerank request to a selected backend.

    `X-Request-Id`, `X-Trace-Id`, and `X-Session-Id` are optional; blank or missing values are
    filled with UUIDs for logging and upstream forwarding.

    Flow: admission -> parse JSON -> retry loop (pick backend -> POST upstream -> metrics/logs).
    Retries exclude failed backends; `5xx` counts as selector failure for the breaker.
    """
    context: GatewayContext = request.app.state.gateway_context
    request_id, trace_id, session_id = _resolve_correlation_ids(request)
    out_headers = _build_outbound_headers(request, request_id, trace_id, session_id)
    admission = context.settings.admission_queue
    retry_cfg = context.settings.retry
    retryable_statuses = retry_cfg.retryable_statuses
    max_attempts = retry_cfg.max_attempts
    queue_wait_start = time.perf_counter()
    ADMISSION_INQUEUE.inc()
    acquired = False
    try:
        await asyncio.wait_for(
            context.queue.acquire(),
            timeout=admission.wait_timeout_sec,
        )
        acquired = True
    except asyncio.TimeoutError:
        ADMISSION_REJECTED.inc()
        log_gateway_event(
            logger,
            logging.WARNING,
            "admission_rejected",
            request_id=request_id,
            trace_id=trace_id,
            session_id=session_id,
            path=_RERANK_PATH,
            status_code=429,
            error={"kind": "AdmissionQueueTimeout"},
            gateway_meta={"wait_timeout_ms": admission.wait_timeout_ms},
        )
        return JSONResponse(status_code=429, content={"error": "Gateway busy, try again"})
    finally:
        ADMISSION_INQUEUE.dec()

    queue_wait_ms = (time.perf_counter() - queue_wait_start) * 1000
    ADMISSION_WAIT.observe(queue_wait_ms)

    try:
        payload = await request.json()
        if _body_contains_correlation_id(payload):
            log_gateway_event(
                logger,
                logging.WARNING,
                "request_rejected_invalid_body",
                request_id=request_id,
                trace_id=trace_id,
                session_id=session_id,
                path=_RERANK_PATH,
                status_code=400,
                error={"kind": "CorrelationIdInBody"},
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Correlation IDs must be provided via headers only: "
                    "X-Request-Id, X-Trace-Id, X-Session-Id"
                },
            )
        parsed = RerankRequest.model_validate(payload)
        req_class = parsed.classify().value

        log_gateway_event(
            logger,
            logging.INFO,
            "gateway_started",
            request_id=request_id,
            trace_id=trace_id,
            session_id=session_id,
            path=_RERANK_PATH,
            queue_wait_ms=queue_wait_ms,
            gateway_meta={
                "backends_count": len(context.settings.backends),
                "admission_max_concurrent": admission.max_concurrent,
                "admission_wait_timeout_ms": admission.wait_timeout_ms,
                "model": parsed.model,
                "request_class": req_class,
                "client_host": getattr(request.client, "host", None),
            },
        )

        excluded: set[str] = set()
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            backend = context.selector.pick(excluded=excluded)
            if backend is None:
                FAILURES.labels(backend="none", reason="no_backend").inc()
                log_gateway_event(
                    logger,
                    logging.WARNING,
                    "routing_no_backend",
                    request_id=request_id,
                    trace_id=trace_id,
                    session_id=session_id,
                    path=_RERANK_PATH,
                    queue_wait_ms=queue_wait_ms,
                    gateway_meta={"attempt": attempt, "excluded": sorted(excluded), **_routing_debug(context.selector, excluded)},
                )
                raise HTTPException(status_code=503, detail="No healthy backend available")

            log_gateway_event(
                logger,
                logging.INFO,
                "routing_pick",
                request_id=request_id,
                trace_id=trace_id,
                session_id=session_id,
                path=_RERANK_PATH,
                backend=backend.name,
                queue_wait_ms=queue_wait_ms,
                gateway_meta={
                    "attempt": attempt,
                    "excluded": sorted(excluded),
                    "decision_reason": context.selector.last_pick_reason,
                    **_routing_debug(context.selector, excluded),
                },
            )

            start = time.perf_counter()
            context.selector.mark_start(backend.name)
            INFLIGHT.labels(backend=backend.name).inc()
            BACKEND_SELECTED.labels(backend=backend.name).inc()
            backend_name = backend.name
            upstream_url = f"{backend.url}{_RERANK_PATH}"

            try:
                upstream = await context.client.post(
                    upstream_url,
                    json=payload,
                    headers=out_headers,
                )
                latency_ms = (time.perf_counter() - start) * 1000
                success = upstream.status_code < 500
                context.selector.mark_result(backend_name, latency_ms, success=success)
                LATENCY.labels(backend=backend_name).observe(latency_ms)
                if upstream.status_code in retryable_statuses and attempt < max_attempts:
                    RETRIES.labels(backend=backend_name).inc()
                    INFLIGHT.labels(backend=backend_name).dec()
                    excluded.add(backend_name)
                    log_gateway_event(
                        logger,
                        logging.WARNING,
                        "backend_retry",
                        request_id=request_id,
                        trace_id=trace_id,
                        session_id=session_id,
                        path=_RERANK_PATH,
                        backend=backend_name,
                        latency_ms=latency_ms,
                        queue_wait_ms=queue_wait_ms,
                        status_code=upstream.status_code,
                        gateway_meta={"attempt": attempt},
                    )
                    continue

                REQUESTS.labels(backend=backend_name, status=str(upstream.status_code), request_class=req_class).inc()
                INFLIGHT.labels(backend=backend_name).dec()
                log_gateway_event(
                    logger,
                    logging.INFO,
                    "request_finished",
                    request_id=request_id,
                    trace_id=trace_id,
                    session_id=session_id,
                    path=_RERANK_PATH,
                    backend=backend_name,
                    latency_ms=latency_ms,
                    queue_wait_ms=queue_wait_ms,
                    status_code=upstream.status_code,
                    gateway_meta={
                        "model": parsed.model,
                        "request_class": req_class,
                        "attempt": attempt,
                    },
                )
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"),
                )
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                context.selector.mark_result(backend_name, latency_ms, success=False)
                FAILURES.labels(backend=backend_name, reason=type(exc).__name__).inc()
                INFLIGHT.labels(backend=backend_name).dec()
                last_exc = exc
                if attempt < max_attempts:
                    RETRIES.labels(backend=backend_name).inc()
                    excluded.add(backend_name)
                    log_gateway_event(
                        logger,
                        logging.WARNING,
                        "backend_retry",
                        request_id=request_id,
                        trace_id=trace_id,
                        session_id=session_id,
                        path=_RERANK_PATH,
                        backend=backend_name,
                        latency_ms=latency_ms,
                        queue_wait_ms=queue_wait_ms,
                        error={"kind": type(exc).__name__},
                        gateway_meta={"attempt": attempt},
                    )
                    continue
                break

        log_gateway_event(
            logger,
            logging.WARNING,
            "request_failed",
            request_id=request_id,
            trace_id=trace_id,
            session_id=session_id,
            path=_RERANK_PATH,
            queue_wait_ms=queue_wait_ms,
            error={"kind": type(last_exc).__name__ if last_exc else "unknown"},
        )
        return JSONResponse(status_code=503, content={"error": "Backends unavailable"})
    finally:
        if acquired:
            context.queue.release()
