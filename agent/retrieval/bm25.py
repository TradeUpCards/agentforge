"""BM25 keyword retriever for hybrid RAG.

Wraps the rank-bm25 library.  The index is built in memory from the
corpus at ingestion time and rebuilt whenever new chunks are added.

The GuidelineChunk type is imported lazily (inside methods) to avoid a
circular import — `agent.document_schemas` imports nothing from this
package.

W2_ARCHITECTURE.md §4.1 — BM25 keyword leg of the retrieval pipeline.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.document_schemas import GuidelineChunk

_logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """Lowercase and split on whitespace / punctuation.

    Intentionally simple — BM25 accuracy at this corpus size (~10 chunks)
    doesn't benefit from stemming or stop-word removal.  Add later if
    corpus grows significantly.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25Retriever:
    """In-memory BM25 index over GuidelineChunk bodies.

    Usage:
        retriever = BM25Retriever()
        retriever.index(corpus_chunks)
        results = retriever.query("metformin CKD", top_k=20)
    """

    def __init__(self) -> None:
        self._chunks: list[Any] = []
        self._bm25: Any = None  # rank_bm25.BM25Okapi instance

    def index(self, chunks: list[Any]) -> None:
        """Build the BM25 index from a list of GuidelineChunk objects.

        Idempotent: calling index() a second time replaces the previous index.

        Args:
            chunks: list of GuidelineChunk (from agent.document_schemas).
        """
        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "rank-bm25 is not installed.  "
                "Add rank-bm25>=0.2.2 to agent/requirements.txt."
            ) from exc

        self._chunks = list(chunks)
        tokenized_corpus = [_tokenize(c.body) for c in self._chunks]
        self._bm25 = BM25Okapi(tokenized_corpus)
        _logger.debug("BM25 index built with %d chunks.", len(self._chunks))

    def query(self, text: str, top_k: int = 20) -> list[Any]:
        """Return up to top_k GuidelineChunks ranked by BM25 score.

        Args:
            text:   Query string (free text clinical query).
            top_k:  Maximum number of results.

        Returns:
            List of GuidelineChunk objects in descending BM25 score order.
            May be shorter than top_k if the index has fewer chunks.

        Raises:
            RuntimeError: if index() has not been called first.
        """
        if self._bm25 is None:
            raise RuntimeError("BM25Retriever.index() must be called before query().")
        if not text.strip():
            return []

        tokens = _tokenize(text)
        scores = self._bm25.get_scores(tokens)

        # Pair each chunk with its score, sort descending, take top_k.
        ranked = sorted(
            zip(self._chunks, scores),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [chunk for chunk, _score in ranked[:top_k]]
