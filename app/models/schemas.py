"""Rerank request model and `request_class` bucketing for metrics labels."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RerankClass(str, Enum):
    """Low-cardinality `request_class` label values (SMALL / MEDIUM / LARGE)."""

    SMALL_RERANK = "SMALL_RERANK"
    MEDIUM_RERANK = "MEDIUM_RERANK"
    LARGE_RERANK = "LARGE_RERANK"


class RerankRequest(BaseModel):
    """vLLM-style rerank request (`model`, `query`, `documents`, optional `top_n`).

    Optional body field ``conversation_id`` is resolved and stripped before validation;
    see ``app/core/conversation.py`` and ``docs/conversation-id.md``.
    """

    model: str
    query: str
    documents: list[str]
    top_n: int | None = None

    def classify(self) -> RerankClass:
        """Map request size to `RerankClass` using document count and total character length."""
        count = len(self.documents)
        total_chars = len(self.query) + sum(len(d) for d in self.documents)
        if count <= 4 and total_chars <= 2048:
            return RerankClass.SMALL_RERANK
        if count <= 32 and total_chars <= 16384:
            return RerankClass.MEDIUM_RERANK
        return RerankClass.LARGE_RERANK
