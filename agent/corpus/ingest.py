"""One-shot ingestion CLI: load corpus → embed → upsert into Qdrant.

Usage:
    python -m agent.corpus.ingest

Idempotent: deletes and recreates the Qdrant collection on each run so
stale chunks cannot accumulate.  Re-running after adding or editing
guideline files will always produce a clean, consistent index.

Environment variables read:
    QDRANT_HOST  (default: localhost)
    QDRANT_PORT  (default: 6333)
    HF_HOME      (default: /app/.hf_cache) — sentence-transformer cache

W2_ARCHITECTURE.md §6.3 — retrieval pipeline ingestion step.
"""

from __future__ import annotations

import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
_logger = logging.getLogger(__name__)


def main() -> None:
    """Load corpus, embed, upsert into Qdrant."""
    _logger.info("=== Corpus ingestion started ===")
    t0 = time.perf_counter()

    # Step 1: Load corpus.
    from agent.corpus.loader import load_corpus

    _logger.info("Loading corpus...")
    chunks = load_corpus()
    _logger.info("Loaded %d chunks.", len(chunks))

    if not chunks:
        _logger.error("No chunks loaded — aborting ingestion.")
        sys.exit(1)

    # Step 2: Ensure Qdrant collection exists (or recreate).
    from qdrant_client import QdrantClient  # type: ignore[import]
    from qdrant_client.models import Distance, VectorParams  # type: ignore[import]

    from agent.retrieval.qdrant_client import (
        COLLECTION_NAME,
        _QDRANT_HOST,
        _QDRANT_PORT,
    )

    _logger.info("Connecting to Qdrant at %s:%d...", _QDRANT_HOST, _QDRANT_PORT)
    client = QdrantClient(host=_QDRANT_HOST, port=_QDRANT_PORT)

    # Delete and recreate for idempotency.
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        _logger.info("Deleting existing collection '%s'...", COLLECTION_NAME)
        client.delete_collection(COLLECTION_NAME)

    _logger.info("Creating collection '%s'...", COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    )

    # Step 3: Embed + upsert.
    _logger.info("Embedding %d chunks...", len(chunks))
    from agent.retrieval.qdrant_client import upsert_chunks

    upsert_chunks(chunks)

    elapsed = time.perf_counter() - t0
    _logger.info(
        "=== Ingestion complete: %d chunks in %.1fs ===", len(chunks), elapsed
    )

    # Step 4: Verify by doing a sample query.
    _logger.info("Verification query: 'metformin diabetes'")
    from agent.retrieval.embeddings import embed
    from agent.retrieval.qdrant_client import query_vectors

    sample_vec = embed(["metformin diabetes"])[0]
    results = query_vectors(sample_vec, top_k=3)
    for r in results:
        _logger.info(
            "  chunk_id=%s score=%.4f", r.get("chunk_id", "?"), r.get("score", 0.0)
        )

    if not results:
        _logger.warning("Verification query returned no results — check Qdrant setup.")
    else:
        _logger.info("Verification OK.")


if __name__ == "__main__":
    main()
