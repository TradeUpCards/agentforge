"""Live integration test for the reranker module.

Gated by COHERE_API_KEY.  Verifies:
  1. Cohere Rerank API returns non-empty rerank scores.
  2. BAAI fallback path produces equivalent shape (list of (chunk, float) tuples).

NOT run in CI (no keys).  Run locally:
    pytest agent/tests/integration/test_rerank_live.py -v -s

W2_ARCHITECTURE.md §4.1 (reranker in retrieval pipeline).
"""

from __future__ import annotations

import os

import pytest

from agent.document_schemas import GuidelineChunk


def _make_test_chunk(n: int) -> GuidelineChunk:
    return GuidelineChunk(
        chunk_id=f"test-chunk-{n}",
        section=f"S{n}",
        source_url=f"https://example.com/test-{n}",
        source_attribution=f"Test attribution {n}.",
        body=(
            f"Chunk {n}: This is a synthetic clinical guideline chunk used for "
            f"reranker integration testing.  It contains terms like metformin, "
            f"diabetes, A1c, blood pressure, and hypertension."
        ),
    )


_TEST_CHUNKS = [_make_test_chunk(i) for i in range(1, 6)]
_TEST_QUERY = "metformin A1c target type 2 diabetes"


# ---------------------------------------------------------------------------
# Cohere live test — skipped unless COHERE_API_KEY is present
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.getenv("COHERE_API_KEY") is None,
    reason="COHERE_API_KEY not set — live Cohere reranker test requires a real key",
)
def test_cohere_rerank_returns_scores() -> None:
    """Cohere Rerank API returns non-empty list with float relevance scores."""
    from agent.retrieval.reranker import _rerank_cohere

    result = _rerank_cohere(_TEST_QUERY, _TEST_CHUNKS)

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) == len(_TEST_CHUNKS), (
        f"Expected {len(_TEST_CHUNKS)} results, got {len(result)}"
    )
    for chunk, score in result:
        assert isinstance(chunk, GuidelineChunk), f"Expected GuidelineChunk, got {type(chunk)}"
        assert isinstance(score, float), f"Expected float score, got {type(score)}"
        assert 0.0 <= score <= 1.0, f"Score {score} is outside [0, 1]"

    # Verify that the result is sorted descending.
    scores = [score for _, score in result]
    assert scores == sorted(scores, reverse=True), "Results should be sorted by score descending"


# ---------------------------------------------------------------------------
# BAAI fallback test — skipped unless sentence-transformers is installed
# ---------------------------------------------------------------------------


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _sentence_transformers_available(),
    reason="sentence-transformers not installed — BAAI reranker test requires it",
)
def test_baai_rerank_produces_equivalent_shape() -> None:
    """BAAI CrossEncoder fallback returns the same list[(chunk, float)] shape
    as the Cohere path.
    """
    from agent.retrieval.reranker import _rerank_baai

    result = _rerank_baai(_TEST_QUERY, _TEST_CHUNKS)

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) == len(_TEST_CHUNKS), (
        f"Expected {len(_TEST_CHUNKS)} results, got {len(result)}"
    )
    for chunk, score in result:
        assert isinstance(chunk, GuidelineChunk), f"Expected GuidelineChunk, got {type(chunk)}"
        assert isinstance(score, float), f"Expected float score, got {type(score)}"

    # Result should be sorted descending.
    scores = [score for _, score in result]
    assert scores == sorted(scores, reverse=True), "Results should be sorted by score descending"


@pytest.mark.skipif(
    not _sentence_transformers_available(),
    reason="sentence-transformers not installed — reranker fallback test requires it",
)
def test_rerank_function_uses_baai_when_no_cohere_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """rerank() silently falls back to BAAI when COHERE_API_KEY is absent."""
    monkeypatch.delenv("COHERE_API_KEY", raising=False)

    # Reload the module to pick up the monkeypatched env.
    import importlib

    import agent.retrieval.reranker as reranker_mod

    importlib.reload(reranker_mod)

    result = reranker_mod.rerank(_TEST_QUERY, _TEST_CHUNKS)

    assert isinstance(result, list)
    assert len(result) == len(_TEST_CHUNKS)
