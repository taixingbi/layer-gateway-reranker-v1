"""Construct `httpx.Timeout` values from millisecond gateway settings."""

from __future__ import annotations

import httpx


def get_timeout(connect_ms: int, read_ms: int) -> httpx.Timeout:
    """Map connect/read ms to seconds; read timeout is reused for write and pool acquire."""
    return httpx.Timeout(
        connect=connect_ms / 1000.0,
        read=read_ms / 1000.0,
        write=read_ms / 1000.0,
        pool=read_ms / 1000.0,
    )
