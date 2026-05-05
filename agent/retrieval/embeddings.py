"""Sentence-transformer embedding wrapper for hybrid RAG.

Model: sentence-transformers/all-MiniLM-L6-v2 (~80 MB download).
Cache path: HF_HOME=/app/.hf_cache (set in Dockerfile / docker-compose).

Singleton pattern: the model is loaded once per process on first call to
embed() and reused for all subsequent calls.  Do NOT import this module
at the top of files that should stay fast on cold import — the import
itself is free; the load happens only on the first embed() call.

W2_ARCHITECTURE.md §4.1 (retrieval pipeline) — dense vector leg.
"""

from __future__ import annotations

import logging
import os

import numpy as np

_logger = logging.getLogger(__name__)

# Respect HF_HOME if set; otherwise default to /app/.hf_cache (the path
# expected inside the agent Docker container).  Local dev keeps models in
# whatever the user's HF_HOME is.
_HF_HOME = os.environ.get("HF_HOME", "/app/.hf_cache")
os.environ.setdefault("HF_HOME", _HF_HOME)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Module-level singleton — loaded lazily on first embed() call.
_model: object | None = None


def _get_model() -> object:
    """Return the cached SentenceTransformer model, loading if necessary.

    Thread-safety note: in production this runs inside a single-process
    asyncio event loop (uvicorn) so the global write is safe.  If the
    service is ever multi-process, replace with a proper singleton guard.
    """
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]

            _logger.info(
                "Loading embedding model %s (HF_HOME=%s)", _MODEL_NAME, _HF_HOME
            )
            _model = SentenceTransformer(_MODEL_NAME, cache_folder=_HF_HOME)
            _logger.info("Embedding model loaded.")
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed.  "
                "Add sentence-transformers>=3.0.0 to agent/requirements.txt."
            ) from exc
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Embed a list of texts; return shape (n, 384) float32 ndarray.

    384 dimensions is the output size of all-MiniLM-L6-v2.  The array
    is suitable for cosine-similarity ranking or upserting into Qdrant.

    Args:
        texts: Non-empty list of strings to embed.

    Returns:
        np.ndarray of shape (len(texts), 384), dtype float32.

    Raises:
        ValueError: if texts is empty.
        RuntimeError: if sentence-transformers cannot be imported.
    """
    if not texts:
        raise ValueError("embed() requires at least one text.")
    model = _get_model()
    # SentenceTransformer.encode() returns ndarray float32 by default.
    result = model.encode(  # type: ignore[union-attr]
        texts, show_progress_bar=False, convert_to_numpy=True
    )
    return np.array(result, dtype=np.float32)


def embedding_dimension() -> int:
    """Return the output dimension of the current model (384 for MiniLM-L6-v2)."""
    return 384
