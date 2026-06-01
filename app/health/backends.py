"""Probe upstream reranker backends for readiness."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx

from app.core.config import BackendConfig

_HEALTH_PATH = "/health"


async def probe_backends(
    backends: Sequence[BackendConfig],
    client: httpx.AsyncClient,
    *,
    health_path: str = _HEALTH_PATH,
) -> dict[str, str]:
    """GET each backend `{url}/health`; return `{name: "healthy"|"unhealthy"}`."""

    async def _probe_one(backend: BackendConfig) -> tuple[str, str]:
        url = f"{backend.url.rstrip('/')}{health_path}"
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return backend.name, "healthy"
        except httpx.HTTPError:
            pass
        return backend.name, "unhealthy"

    if not backends:
        return {}

    results = await asyncio.gather(*(_probe_one(b) for b in backends))
    return dict(results)


def ready_payload(backend_status: dict[str, str]) -> tuple[dict[str, object], int]:
    """Build `/ready` JSON body and HTTP status from probe results."""
    total = len(backend_status)
    healthy = sum(1 for status in backend_status.values() if status == "healthy")
    all_healthy = total > 0 and healthy == total
    body: dict[str, object] = {
        "status": "ready" if all_healthy else "not_ready",
        "healthy_backends": healthy,
        "total_backends": total,
        "backends": backend_status,
    }
    return body, 200 if all_healthy else 503
