"""Thin Qdrant wrapper for the hybrid RAG retrieval pipeline.

Connection parameters come from environment variables:
    QDRANT_HOST  (default: "localhost")
    QDRANT_PORT  (default: 6333)

Collection name is fixed to COLLECTION_NAME ("clinical_guidelines") and is
dedicated to this corpus.  A separate collection can be added for other
corpora without changing this module.

Public API (all functions are module-level; no class required):
    ensure_collection()          — create collection if absent
    upsert_chunks(chunks)        — embed + upsert GuidelineChunk list
    query_vectors(embedding, k)  — ANN search; returns top-k with payloads

Point ID stability (ride-along fix from round-2 review): Qdrant requires
uint64 point IDs.  We derive them from SHA-256(chunk_id) — specifically the
first 8 bytes interpreted as a big-endian unsigned integer — so the mapping is
deterministic across process restarts regardless of PYTHONHASHSEED.
hash() is intentionally NOT used: it is salted per-process by Python and
would assign different numeric IDs to the same chunk after a container
restart, causing orphaned points to accumulate in the collection.

W2_ARCHITECTURE.md §6.1 (Qdrant choice), §6.3 (retrieval pipeline).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

_logger = logging.getLogger(__name__)

COLLECTION_NAME = "clinical_guidelines"
_VECTOR_SIZE = 384  # all-MiniLM-L6-v2 output dimension

_QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
_QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))

# Module-level singleton; created lazily on first use.
_client: Any | None = None


def _get_client() -> Any:
    """Return the cached QdrantClient, creating it if necessary."""
    global _client
    if _client is None:
        try:
            from qdrant_client import QdrantClient  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client is not installed.  "
                "Add qdrant-client>=1.10.0 to agent/requirements.txt."
            ) from exc
        _client = QdrantClient(host=_QDRANT_HOST, port=_QDRANT_PORT)
        _logger.info(
            "QdrantClient connected to %s:%d", _QDRANT_HOST, _QDRANT_PORT
        )
    return _client


def ensure_collection() -> None:
    """Create the clinical_guidelines collection if it does not already exist.

    Uses cosine distance (appropriate for sentence-transformer embeddings
    that are not L2-normalized).  Safe to call repeatedly — idempotent.
    """
    try:
        from qdrant_client.models import (  # type: ignore[import]
            Distance,
            VectorParams,
        )
    except ImportError as exc:
        raise RuntimeError("qdrant-client not installed.") from exc

    client = _get_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=_VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )
        _logger.info("Created Qdrant collection '%s'.", COLLECTION_NAME)
    else:
        _logger.debug("Qdrant collection '%s' already exists.", COLLECTION_NAME)


def upsert_chunks(chunks: list[Any]) -> None:
    """Embed and upsert a list of GuidelineChunk into Qdrant.

    Each chunk's chunk_id is used as the point ID (deterministic, stable).
    The full chunk payload is stored so retrieval can reconstruct citations
    without a second lookup.

    Args:
        chunks: list of GuidelineChunk (from agent.document_schemas).
    """
    if not chunks:
        return

    try:
        from qdrant_client.models import PointStruct  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError("qdrant-client not installed.") from exc

    from agent.retrieval.embeddings import embed

    texts = [c.body for c in chunks]
    vectors = embed(texts)

    points = []
    for idx, chunk in enumerate(chunks):
        # Derive a stable uint64 point ID from SHA-256(chunk_id).
        # Using hash() here would be wrong — Python salts it with
        # PYTHONHASHSEED, so the same chunk_id maps to a different integer
        # after every process restart, producing orphaned Qdrant points.
        # SHA-256 first-8-bytes is collision-resistant for a corpus of
        # ≤10k chunks and stable across restarts.
        point_id = int.from_bytes(
            hashlib.sha256(chunk.chunk_id.encode()).digest()[:8], "big"
        )
        points.append(
            PointStruct(
                id=point_id,
                vector=vectors[idx].tolist(),
                payload={
                    "chunk_id": chunk.chunk_id,
                    "section": chunk.section,
                    "source_url": chunk.source_url,
                    "source_attribution": chunk.source_attribution,
                    "body": chunk.body,
                },
            )
        )

    client = _get_client()
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    _logger.info("Upserted %d chunks into Qdrant '%s'.", len(points), COLLECTION_NAME)


def query_vectors(embedding: Any, top_k: int = 20) -> list[dict[str, Any]]:
    """Perform ANN vector search against the clinical_guidelines collection.

    Args:
        embedding: 1-D float array of length 384 (all-MiniLM-L6-v2 output).
        top_k:     Number of nearest-neighbor results to return.

    Returns:
        List of dicts, each containing the payload fields plus 'score'.
        Empty list if Qdrant is unreachable or the collection is empty.
    """
    client = _get_client()
    try:
        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=embedding.tolist() if hasattr(embedding, "tolist") else list(embedding),
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        _logger.warning("Qdrant query failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for hit in results:
        payload = dict(hit.payload or {})
        payload["score"] = hit.score
        out.append(payload)
    return out
