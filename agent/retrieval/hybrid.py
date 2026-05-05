"""Top-level hybrid search combining BM25 + vector retrieval + reranker.

Pipeline (W2_ARCHITECTURE.md §4.1, §6.3):
    1. BM25 top-20 (keyword exact-match recall)
    2. Vector top-20 (semantic paraphrase recall)
    3. Union with deduplication by chunk_id
    4. Rerank (Cohere primary, BAAI fallback)
    5. Return top_k_final chunks as GuidelineChunk objects

The corpus and BM25 index are loaded once per process lifetime via
module-level initialization triggered by the first call to hybrid_search().

Observability: no query text or chunk body text is logged (§8.3).
Only structural metrics (candidate counts, top score) go to the log.

Testing note: module-level names `embed`, `query_vectors`, and `rerank` are
imported eagerly so unit tests can patch them via
`patch("agent.retrieval.hybrid.embed")` etc. without attribute errors.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

# Module-level state — initialized lazily on first hybrid_search() call.
_corpus: list[Any] | None = None
_bm25_retriever: Any | None = None
_use_qdrant: bool = True  # set to False if Qdrant is unreachable


# ---------------------------------------------------------------------------
# Patchable module-level wrappers for testability
# ---------------------------------------------------------------------------
# These thin wrappers import from the real sub-modules on first call.
# Unit tests patch these names directly:
#   patch("agent.retrieval.hybrid.embed", ...)
#   patch("agent.retrieval.hybrid.query_vectors", ...)
#   patch("agent.retrieval.hybrid.rerank", ...)


def embed(texts: list[str]) -> Any:
    """Module-level embed wrapper — patchable by unit tests."""
    from agent.retrieval.embeddings import embed as _embed  # noqa: PLC0415
    return _embed(texts)


def query_vectors(embedding: Any, top_k: int = 20) -> list[dict[str, Any]]:
    """Module-level query_vectors wrapper — patchable by unit tests."""
    from agent.retrieval.qdrant_client import query_vectors as _qv  # noqa: PLC0415
    return _qv(embedding, top_k=top_k)


def rerank(query: str, chunks: list[Any]) -> list[tuple[Any, float]]:
    """Module-level rerank wrapper — patchable by unit tests."""
    from agent.retrieval.reranker import rerank as _rerank  # noqa: PLC0415
    return _rerank(query, chunks)


def _load_corpus_and_index() -> None:
    """Load the guideline corpus and build the BM25 index (once per process)."""
    global _corpus, _bm25_retriever

    if _corpus is not None:
        return  # Already loaded.

    from agent.corpus.loader import load_corpus
    from agent.retrieval.bm25 import BM25Retriever

    _corpus = load_corpus()
    _bm25_retriever = BM25Retriever()
    _bm25_retriever.index(_corpus)
    _logger.info(
        "Hybrid RAG: corpus loaded (%d chunks), BM25 index built.", len(_corpus)
    )


def _query_qdrant(query: str, top_k: int) -> list[Any]:
    """Attempt Qdrant vector search; return empty list on any failure.

    Failure modes handled silently here:
    - Qdrant not running (connection refused)
    - Collection not yet ingested (empty result)
    - qdrant-client not installed (ImportError)
    """
    global _use_qdrant
    if not _use_qdrant:
        return []

    try:
        query_vec = embed([query])[0]
    except (ImportError, RuntimeError) as exc:
        _logger.warning("Embedding unavailable: %s", exc)
        _use_qdrant = False
        return []

    try:
        hits = query_vectors(query_vec, top_k=top_k)
    except Exception as exc:
        _logger.warning(
            "Qdrant query failed (falling back to BM25-only): %s",
            type(exc).__name__,
        )
        _use_qdrant = False
        return []

    # Reconstruct GuidelineChunk objects from Qdrant payloads.
    from agent.document_schemas import GuidelineChunk  # noqa: PLC0415

    chunks: list[Any] = []
    for hit in hits:
        try:
            chunks.append(
                GuidelineChunk(
                    chunk_id=hit["chunk_id"],
                    section=hit.get("section", ""),
                    source_url=hit.get("source_url", ""),
                    source_attribution=hit.get("source_attribution", ""),
                    body=hit["body"],
                )
            )
        except Exception:
            pass  # Malformed payload — skip without crashing.
    return chunks


def hybrid_search(query: str, top_k_final: int = 5) -> list[Any]:
    """Run hybrid BM25 + vector retrieval, rerank, and return top chunks.

    Args:
        query:       Clinical query string (free text).
        top_k_final: Number of chunks to return after reranking.

    Returns:
        List of at most top_k_final GuidelineChunk objects, sorted by
        reranker score descending.  May return fewer if the corpus has
        fewer than top_k_final chunks.

    Observability (§8.3): logs only chunk counts and top score, never
    the query string or chunk body text.
    """
    _load_corpus_and_index()

    assert _bm25_retriever is not None  # guaranteed by _load_corpus_and_index

    # --- BM25 leg ---
    bm25_top = _bm25_retriever.query(query, top_k=20)

    # --- Vector leg ---
    vector_top = _query_qdrant(query, top_k=20)

    # --- Union with dedup by chunk_id ---
    seen: set[str] = set()
    candidates: list[Any] = []
    for chunk in bm25_top + vector_top:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            candidates.append(chunk)

    _logger.debug(
        "Hybrid RAG: bm25=%d vector=%d union=%d",
        len(bm25_top),
        len(vector_top),
        len(candidates),
    )

    if not candidates:
        return []

    # --- Rerank ---
    reranked = rerank(query, candidates)

    # Slice to top_k_final.
    top = [chunk for chunk, _score in reranked[:top_k_final]]

    if reranked:
        _logger.debug(
            "Hybrid RAG: returned %d chunks, top_score=%.4f",
            len(top),
            reranked[0][1] if reranked else 0.0,
        )

    return top
