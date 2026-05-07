"""FastAPI app factory: lifespan wiring, `/health`, `/ready`, `/metrics`, and uvicorn entry."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response

from app.api.rerank import GatewayContext, router as rerank_router
from app.core.config import get_settings
from app.core.logging import build_logging_config, log_gateway_event
from app.metrics.prometheus import render_metrics
from app.proxy.client import get_timeout
from app.routing.selector import BackendSelector

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared `httpx` client, routing selector, admission semaphore, and shutdown."""
    settings = get_settings()
    selector = BackendSelector(settings.backends, settings.routing, settings.circuit_breaker)
    timeout = get_timeout(settings.timeouts.connect_ms, settings.timeouts.read_ms)
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    client = httpx.AsyncClient(timeout=timeout, limits=limits)
    queue = asyncio.Semaphore(settings.admission_queue.max_concurrent)
    app.state.gateway_context = GatewayContext(settings=settings, selector=selector, client=client, queue=queue)
    log_gateway_event(
        logger,
        logging.INFO,
        "gateway_process_started",
        gateway_meta={
            "backends_count": len(settings.backends),
            "admission_max_concurrent": settings.admission_queue.max_concurrent,
            "admission_wait_timeout_ms": settings.admission_queue.wait_timeout_ms,
        },
    )
    yield
    await client.aclose()


app = FastAPI(title="layer-gateway-reranker-v1", lifespan=lifespan)
app.include_router(rerank_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Kubernetes-style liveness payload."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Kubernetes-style readiness payload."""
    return {"status": "ready"}


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus text exposition (`/metrics`)."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


def run() -> None:
    """Run uvicorn with host/port and logging config from `get_settings()`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        log_config=build_logging_config(level_name=settings.log.level, json_logs=settings.log.json),
        log_level=settings.log.level.lower(),
    )


if __name__ == "__main__":
    run()
