"""Reranker for hybrid RAG candidates.

Primary path:   Cohere Rerank API (gated by COHERE_API_KEY env var).
Fallback path:  BAAI/bge-reranker-v2 via sentence-transformers CrossEncoder.

Both paths expose the same interface:
    rerank(query: str, chunks: list[GuidelineChunk]) -> list[tuple[GuidelineChunk, float]]

The output list is sorted by score descending.  The caller (hybrid.py) slices
to top_k_final after reranking.

Observability (W2_ARCHITECTURE.md §8.3 no-PHI-in-logs):
- Only chunk_id and score are logged — never chunk body text.
- The query is not logged (it may contain patient context).

W2_ARCHITECTURE.md §4.1 — reranker step in retrieval pipeline.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_logger = logging.getLogger(__name__)

_COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")
_BAAI_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
# Note: BAAI/bge-reranker-v2-m3 is the ideal fallback; however ms-marco-MiniLM-L-6-v2
# is the most widely available cross-encoder on HuggingFace without Qwen dependency.
# The interface is identical regardless of which cross-encoder is used.


# Module-level singleton for BAAI cross-encoder (loaded lazily).
_cross_encoder: Any | None = None


def _get_cross_encoder() -> Any:
    """Return the cached CrossEncoder model (BAAI reranker fallback)."""
    global _cross_encoder
    if _cross_encoder is None:
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed for BAAI reranker fallback."
            ) from exc
        hf_home = os.environ.get("HF_HOME", "/app/.hf_cache")
        _logger.info(
            "Loading BAAI reranker fallback model %s (HF_HOME=%s)",
            _BAAI_RERANKER_MODEL,
            hf_home,
        )
        _cross_encoder = CrossEncoder(_BAAI_RERANKER_MODEL, cache_folder=hf_home)
        _logger.info("BAAI reranker model loaded.")
    return _cross_encoder


def _rerank_cohere(query: str, chunks: list[Any]) -> list[tuple[Any, float]]:
    """Rerank via Cohere Rerank API.

    Returns list of (chunk, score) sorted by score descending.
    Raises on API error so caller falls back to BAAI.
    """
    import cohere  # type: ignore[import]

    co = cohere.Client(api_key=_COHERE_API_KEY)
    documents = [c.body for c in chunks]
    response = co.rerank(
        query=query,
        documents=documents,
        model="rerank-english-v3.0",
        top_n=len(documents),
    )
    # Build (chunk, score) pairs from Cohere result order.
    scored: list[tuple[Any, float]] = []
    for result in response.results:
        chunk = chunks[result.index]
        scored.append((chunk, result.relevance_score))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def _rerank_baai(query: str, chunks: list[Any]) -> list[tuple[Any, float]]:
    """Rerank via local BAAI/bge-reranker CrossEncoder fallback.

    Returns list of (chunk, score) sorted by score descending.
    """
    cross_encoder = _get_cross_encoder()
    pairs = [(query, c.body) for c in chunks]
    raw_scores = cross_encoder.predict(pairs)
    # raw_scores may be an ndarray (.tolist()) or a plain list (in test mocks).
    scores: list[float] = (
        raw_scores.tolist() if hasattr(raw_scores, "tolist") else list(raw_scores)
    )
    scored = list(zip(chunks, scores))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def rerank(query: str, chunks: list[Any]) -> list[tuple[Any, float]]:
    """Rerank candidate chunks by relevance to query.

    Tries Cohere first (if COHERE_API_KEY is set).  Falls back to BAAI
    CrossEncoder if Cohere is unavailable, errors, or the key is absent.

    Observability: logs chunk_id + score only.  Never logs query text or
    chunk body text (W2_ARCHITECTURE.md §8.3).

    Args:
        query:  Clinical query string.
        chunks: Candidate GuidelineChunk objects to rerank.

    Returns:
        List of (GuidelineChunk, float) tuples sorted by score descending.
        Empty list if chunks is empty.
    """
    if not chunks:
        return []

    if _COHERE_API_KEY:
        try:
            result = _rerank_cohere(query, chunks)
            _logger.debug(
                "Cohere rerank: %s",
                [(c.chunk_id, round(s, 4)) for c, s in result[:5]],
            )
            return result
        except Exception as exc:
            _logger.warning(
                "Cohere rerank failed (%s), falling back to BAAI.", type(exc).__name__
            )

    # BAAI fallback
    try:
        result = _rerank_baai(query, chunks)
        _logger.debug(
            "BAAI rerank: %s",
            [(c.chunk_id, round(s, 4)) for c, s in result[:5]],
        )
        return result
    except (ImportError, RuntimeError) as exc:
        # Fix #3 (obs-sec): Neither Cohere nor BAAI is available.
        # Emit an observable signal — Langfuse span tag + structured log
        # sentinel — so operators can alert on degraded retrieval quality.
        # Alert filter: search for 'RERANKER_FALLBACK_IDENTITY' in Langfuse
        # metadata or in log aggregation.  Identity order means BM25-ranked
        # results are returned without cross-encoder re-scoring.
        _logger.warning(
            "RERANKER_FALLBACK_IDENTITY exc_type=%s n_candidates=%d",
            type(exc).__name__,
            len(chunks),
        )
        try:
            from langfuse import get_client  # noqa: PLC0415

            get_client().update_current_span(
                metadata={
                    "reranker_mode": "identity_fallback",
                    "reranker_exc_type": type(exc).__name__,
                }
            )
        except Exception:
            # Langfuse unavailability must never break retrieval.
            pass
        return [(c, 0.0) for c in chunks]
