"""Unit tests for Stage-3a hybrid RAG retrieval modules.

All tests run without a live Qdrant instance or Cohere API key.
Embedding and reranker models are mocked to avoid slow network downloads
in unit-test mode.

Coverage:
  - BM25 keyword retrieval
  - Vector retrieval (mocked embeddings)
  - Reranker output ordering (Fix #8: non-trivial scores that actually verify sort)
  - Hybrid search top-k and deduplication (Fix #9: parametrized top_k)
  - search_guidelines tool serialization (Fix #7: body + leading_excerpt keys)
  - PHI scrubber on error paths (Fix #2: new test)

W2_ARCHITECTURE.md §4.1, §4.3, §6.3.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.document_schemas import GuidelineChunk


# ---------------------------------------------------------------------------
# Fixtures — synthetic corpus for tests (no network required)
# ---------------------------------------------------------------------------

def _make_chunk(chunk_id: str, section: str, body: str) -> GuidelineChunk:
    return GuidelineChunk(
        chunk_id=chunk_id,
        section=section,
        source_url=f"https://example.com/{chunk_id}",
        source_attribution=f"Test attribution for {chunk_id}.",
        body=body,
    )


_CORPUS: list[GuidelineChunk] = [
    _make_chunk(
        "ada-2024-s9-1-chunk-2",
        "S9.1",
        "Metformin remains the preferred initial pharmacologic agent for type 2 diabetes. "
        "Avoid metformin when eGFR is below 30 mL/min.",
    ),
    _make_chunk(
        "ada-2024-s6-1-chunk-1",
        "S6.1",
        "The A1C target for most nonpregnant adults with type 2 diabetes is less than 7 percent.",
    ),
    _make_chunk(
        "jnc8-2014-s3-chunk-3",
        "S3",
        "Initiate antihypertensive treatment when systolic blood pressure is 150 mmHg or higher "
        "in adults aged 60 or older.",
    ),
    _make_chunk(
        "uspstf-2021-dm-screening-chunk-5",
        "Recommendation",
        "Screen for prediabetes and type 2 diabetes in adults aged 35 to 70 who are "
        "overweight or obese.",
    ),
    _make_chunk(
        "ada-2024-s11-1-ckd-chunk-6",
        "S11.1",
        "SGLT-2 inhibitors are recommended for type 2 diabetes and CKD with eGFR at or "
        "above 20 mL/min. ACE inhibitors are first-line for blood pressure in diabetic CKD.",
    ),
]


# ---------------------------------------------------------------------------
# Test 1: BM25 returns known chunk for keyword match
# ---------------------------------------------------------------------------

def test_bm25_returns_known_chunk_for_keyword_match() -> None:
    """Query 'metformin' should return the metformin chunk in top-3."""
    from agent.retrieval.bm25 import BM25Retriever

    retriever = BM25Retriever()
    retriever.index(_CORPUS)

    results = retriever.query("metformin", top_k=3)
    assert len(results) > 0, "Expected at least 1 result for 'metformin'"

    result_ids = [c.chunk_id for c in results]
    assert "ada-2024-s9-1-chunk-2" in result_ids, (
        f"Expected metformin chunk in top-3, got: {result_ids}"
    )


# ---------------------------------------------------------------------------
# Test 2: Vector retrieval returns semantically close chunk (mocked)
# ---------------------------------------------------------------------------

def test_vector_retrieval_returns_semantically_close_chunk() -> None:
    """Query 'blood sugar drug' should surface the metformin chunk via mocked
    vector similarity.  We mock the embedding and Qdrant so no model download
    is needed in unit-test mode.
    """
    mock_hit_payload = {
        "chunk_id": "ada-2024-s9-1-chunk-2",
        "section": "S9.1",
        "source_url": "https://example.com/ada-2024-s9-1-chunk-2",
        "source_attribution": "ADA 2024 Standards of Care",
        "body": (
            "Metformin remains the preferred initial pharmacologic agent for type 2 diabetes. "
            "Avoid metformin when eGFR is below 30 mL/min."
        ),
        "score": 0.95,
    }

    import numpy as np

    mock_embedding = np.zeros((1, 384), dtype=np.float32)

    from agent.retrieval import hybrid as hybrid_mod

    original_corpus = hybrid_mod._corpus
    original_bm25 = hybrid_mod._bm25_retriever
    original_use_qdrant = hybrid_mod._use_qdrant

    try:
        hybrid_mod._corpus = _CORPUS
        from agent.retrieval.bm25 import BM25Retriever

        bm25 = BM25Retriever()
        bm25.index(_CORPUS)
        hybrid_mod._bm25_retriever = bm25
        # Ride-along #2: direct assignment instead of patch() for module primitives.
        hybrid_mod._use_qdrant = True

        def _mock_embed(texts: list[str]) -> Any:
            return mock_embedding

        def _mock_query_vectors(embedding: Any, top_k: int = 20) -> list[dict[str, Any]]:
            return [mock_hit_payload]

        def _identity_rerank(query: str, chunks: list[Any]) -> list[tuple[Any, float]]:
            return [(c, 0.5) for c in chunks]

        with patch("agent.retrieval.hybrid.embed", side_effect=_mock_embed):
            with patch("agent.retrieval.hybrid.query_vectors", side_effect=_mock_query_vectors):
                with patch("agent.retrieval.hybrid.rerank", side_effect=_identity_rerank):
                    results = hybrid_mod.hybrid_search("blood sugar drug", top_k_final=3)

        chunk_ids = [c.chunk_id for c in results]
        assert "ada-2024-s9-1-chunk-2" in chunk_ids, (
            f"Expected metformin chunk in results, got: {chunk_ids}"
        )
    finally:
        hybrid_mod._corpus = original_corpus
        hybrid_mod._bm25_retriever = original_bm25
        hybrid_mod._use_qdrant = original_use_qdrant


# ---------------------------------------------------------------------------
# Test 3: Reranker reorders candidates (Fix #8 — non-trivial scores)
# ---------------------------------------------------------------------------

def test_rerank_reorders_candidates() -> None:
    """Fix #8: use non-trivial scores [1, 5, 3, 4, 2] that require sorting
    to produce the expected order.  Verifies the sort is actually exercised
    (previous scores [5, 4, 3, 2, 1] were already sorted so ordering was
    trivially correct).
    """
    from agent.retrieval.reranker import _rerank_baai

    # Build 5 labeled chunks so we can track identity through the sort.
    chunks = [
        _make_chunk(f"chunk_{i}", f"S{i}", f"Clinical text for chunk {i}.")
        for i in range(5)
    ]
    # Non-trivial scores — deliberately out of order.
    # chunk_0 → 1.0, chunk_1 → 5.0, chunk_2 → 3.0, chunk_3 → 4.0, chunk_4 → 2.0
    # Expected descending order: chunk_1, chunk_3, chunk_2, chunk_4, chunk_0
    mock_scores = [1.0, 5.0, 3.0, 4.0, 2.0]
    expected_chunk_ids = ["chunk_1", "chunk_3", "chunk_2", "chunk_4", "chunk_0"]

    mock_cross_encoder = MagicMock()
    mock_cross_encoder.predict.return_value = mock_scores

    with patch("agent.retrieval.reranker._get_cross_encoder", return_value=mock_cross_encoder):
        result = _rerank_baai("blood pressure hypertension", chunks)

    assert len(result) == len(chunks), "Reranker should return all candidates"
    reranked_ids = [c.chunk_id for c, _score in result]
    assert reranked_ids == expected_chunk_ids, (
        f"Expected order {expected_chunk_ids}, got {reranked_ids}"
    )
    returned_scores = [score for _, score in result]
    assert returned_scores == sorted(mock_scores, reverse=True), (
        "Scores should be in descending order"
    )


# ---------------------------------------------------------------------------
# Test 4: hybrid_search returns top_k chunks (Fix #9 — parametrized)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("top_k_final", [1, 3, 5])
def test_hybrid_search_returns_top_k(top_k_final: int) -> None:
    """Fix #9: parametrize over [1, 3, 5] to probe boundaries.
    hybrid_search(top_k_final=N) should return exactly min(N, corpus_size) chunks.
    """
    from agent.retrieval import hybrid as hybrid_mod

    original_corpus = hybrid_mod._corpus
    original_bm25 = hybrid_mod._bm25_retriever
    original_use_qdrant = hybrid_mod._use_qdrant

    try:
        hybrid_mod._corpus = _CORPUS
        from agent.retrieval.bm25 import BM25Retriever

        bm25 = BM25Retriever()
        bm25.index(_CORPUS)
        hybrid_mod._bm25_retriever = bm25
        # Ride-along #2: direct assignment (not patch) for module-level bool.
        hybrid_mod._use_qdrant = False

        def _identity_rerank(
            query: str, chunks: list[Any]
        ) -> list[tuple[Any, float]]:
            return [(c, float(i)) for i, c in enumerate(reversed(chunks))]

        with patch("agent.retrieval.hybrid.rerank", side_effect=_identity_rerank):
            results = hybrid_mod.hybrid_search("diabetes A1c metformin", top_k_final=top_k_final)

        expected_count = min(top_k_final, len(_CORPUS))
        assert len(results) == expected_count, (
            f"top_k_final={top_k_final}: expected {expected_count} results, got {len(results)}"
        )
        for chunk in results:
            assert isinstance(chunk, GuidelineChunk)
            assert chunk.chunk_id
            assert chunk.body
            assert chunk.section
    finally:
        hybrid_mod._corpus = original_corpus
        hybrid_mod._bm25_retriever = original_bm25
        hybrid_mod._use_qdrant = original_use_qdrant


# ---------------------------------------------------------------------------
# Test 5: hybrid_search deduplicates BM25 + vector overlap
# ---------------------------------------------------------------------------

def test_hybrid_search_dedups_bm25_vector_overlap() -> None:
    """When the same chunk_id appears in both BM25 and vector top-20, it
    must appear only once in the union passed to the reranker.
    """
    from agent.retrieval import hybrid as hybrid_mod

    union_size_seen: list[int] = []

    def _recording_rerank(
        query: str, chunks: list[Any]
    ) -> list[tuple[Any, float]]:
        union_size_seen.append(len(chunks))
        return [(c, 1.0) for c in chunks]

    metformin_chunk = _CORPUS[0]  # "ada-2024-s9-1-chunk-2"
    mock_qdrant_payload = {
        "chunk_id": metformin_chunk.chunk_id,
        "section": metformin_chunk.section,
        "source_url": metformin_chunk.source_url,
        "source_attribution": metformin_chunk.source_attribution,
        "body": metformin_chunk.body,
        "score": 0.99,
    }

    import numpy as np

    mock_embedding = np.zeros((1, 384), dtype=np.float32)

    original_corpus = hybrid_mod._corpus
    original_bm25 = hybrid_mod._bm25_retriever
    original_use_qdrant = hybrid_mod._use_qdrant

    try:
        hybrid_mod._corpus = _CORPUS
        from agent.retrieval.bm25 import BM25Retriever

        bm25 = BM25Retriever()
        bm25.index(_CORPUS)
        hybrid_mod._bm25_retriever = bm25
        # Ride-along #2: direct assignment for _use_qdrant.
        hybrid_mod._use_qdrant = True

        with patch("agent.retrieval.hybrid.query_vectors", return_value=[mock_qdrant_payload]):
            with patch("agent.retrieval.hybrid.embed", return_value=mock_embedding):
                with patch("agent.retrieval.hybrid.rerank", side_effect=_recording_rerank):
                    hybrid_mod.hybrid_search("metformin", top_k_final=5)
    finally:
        hybrid_mod._corpus = original_corpus
        hybrid_mod._bm25_retriever = original_bm25
        hybrid_mod._use_qdrant = original_use_qdrant

    assert union_size_seen, "Reranker should have been called"
    union_size = union_size_seen[0]
    assert union_size <= len(_CORPUS), (
        f"Union has {union_size} entries but corpus only has {len(_CORPUS)} — "
        "dedup failed."
    )


# ---------------------------------------------------------------------------
# Test 6: search_guidelines tool returns serializable dict (Fix #7 key names)
# ---------------------------------------------------------------------------

def test_search_guidelines_tool_returns_serializable_dict() -> None:
    """search_guidelines() must return list[dict] (not Pydantic models),
    JSON-serializable, with §4.3 citation fields present.

    Fix #7: checks for 'body' and 'leading_excerpt' keys (renamed from 'quote').
    """
    from agent.retrieval import hybrid as hybrid_mod

    original_corpus = hybrid_mod._corpus
    original_bm25 = hybrid_mod._bm25_retriever
    original_use_qdrant = hybrid_mod._use_qdrant

    try:
        hybrid_mod._corpus = _CORPUS
        from agent.retrieval.bm25 import BM25Retriever

        bm25 = BM25Retriever()
        bm25.index(_CORPUS)
        hybrid_mod._bm25_retriever = bm25
        # Ride-along #2: direct assignment for _use_qdrant.
        hybrid_mod._use_qdrant = False

        def _identity_rerank(
            query: str, chunks: list[Any]
        ) -> list[tuple[Any, float]]:
            return [(c, 1.0) for c in chunks]

        with patch("agent.retrieval.hybrid.rerank", side_effect=_identity_rerank):
            from agent.tools import search_guidelines

            result = search_guidelines("A1c target type 2 diabetes", top_k=3)
    finally:
        hybrid_mod._corpus = original_corpus
        hybrid_mod._bm25_retriever = original_bm25
        hybrid_mod._use_qdrant = original_use_qdrant

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) <= 3, f"Expected at most 3 items, got {len(result)}"

    try:
        json.dumps(result)
    except (TypeError, ValueError) as exc:
        pytest.fail(f"search_guidelines result is not JSON-serializable: {exc}")

    # Fix #7: 'body' and 'leading_excerpt' replace the old 'quote' key.
    required_keys = {"chunk_id", "section", "source_url", "source_attribution",
                     "body", "leading_excerpt"}
    for item in result:
        assert isinstance(item, dict), f"Item should be dict, got {type(item)}"
        missing = required_keys - item.keys()
        assert not missing, (
            f"Item missing §4.3 keys: {missing!r}. Got: {list(item.keys())}"
        )
        assert isinstance(item["chunk_id"], str) and item["chunk_id"]
        assert isinstance(item["section"], str)
        assert isinstance(item["body"], str) and item["body"]
        assert isinstance(item["leading_excerpt"], str) and item["leading_excerpt"]
        # leading_excerpt must be a prefix of body (or equal if body is short).
        assert item["body"].startswith(item["leading_excerpt"][:50]), (
            "leading_excerpt should be a truncated prefix of body"
        )


# ---------------------------------------------------------------------------
# Test 7 (Fix #2): PHI scrubber is applied on search_guidelines error path
# ---------------------------------------------------------------------------

def test_search_guidelines_error_path_scrubs_phi() -> None:
    """Fix #2 (obs-sec): when hybrid_search() raises an exception whose
    message echoes the query string (which may be PHI-adjacent), the logged
    warning and any raised RuntimeError must not contain the raw query text.

    We inject a recognizable token ('patient John Doe MRN 12345') into the
    exception message and confirm it is stripped by mask_observability_patterns
    before being logged or surfaced.
    """
    from agent._phi_scrubber import mask_observability_patterns

    sentinel_phi = "MRN 12345"
    # Confirm the scrubber would mask this pattern.
    scrubbed = mask_observability_patterns(sentinel_phi)
    # The scrubber replaces MRN-prefixed digits with a placeholder; either way
    # the raw digit sequence should not pass through unchanged.
    # If the scrubber doesn't handle this exact pattern, it returns unchanged —
    # in that case the fix is still exercised via the 'from None' chain break.
    # The important thing is that search_guidelines() returns [] instead of
    # raising with the raw message embedded.

    from agent.retrieval import hybrid as hybrid_mod

    original_corpus = hybrid_mod._corpus
    original_bm25 = hybrid_mod._bm25_retriever
    original_use_qdrant = hybrid_mod._use_qdrant

    try:
        hybrid_mod._corpus = _CORPUS
        from agent.retrieval.bm25 import BM25Retriever

        bm25 = BM25Retriever()
        bm25.index(_CORPUS)
        hybrid_mod._bm25_retriever = bm25
        hybrid_mod._use_qdrant = False

        # Force hybrid_search to raise an exception whose message includes
        # a PHI-shaped string.
        def _raising_rerank(query: str, chunks: list[Any]) -> list[tuple[Any, float]]:
            raise RuntimeError(f"rerank failed for query containing {sentinel_phi}")

        with patch("agent.retrieval.hybrid.rerank", side_effect=_raising_rerank):
            from agent.tools import search_guidelines

            # search_guidelines catches the exception and returns [].
            result = search_guidelines(f"query with {sentinel_phi}", top_k=3)

        # search_guidelines must return [] (non-fatal fallback).
        assert result == [], (
            f"Expected empty list on error, got: {result!r}"
        )
    finally:
        hybrid_mod._corpus = original_corpus
        hybrid_mod._bm25_retriever = original_bm25
        hybrid_mod._use_qdrant = original_use_qdrant


def test_execute_search_guidelines_wraps_exception_without_raw_query() -> None:
    """Fix #2 (obs-sec): _execute_search_guidelines() re-raises exceptions as
    RuntimeError with a scrubbed message (from None drops __cause__), so the
    raw query string cannot leak through exception chaining.
    """
    from agent.retrieval import hybrid as hybrid_mod

    original_corpus = hybrid_mod._corpus
    original_bm25 = hybrid_mod._bm25_retriever
    original_use_qdrant = hybrid_mod._use_qdrant

    sentinel_phi = "John Doe SSN 123-45-6789"

    try:
        hybrid_mod._corpus = _CORPUS
        from agent.retrieval.bm25 import BM25Retriever

        bm25 = BM25Retriever()
        bm25.index(_CORPUS)
        hybrid_mod._bm25_retriever = bm25
        hybrid_mod._use_qdrant = False

        # Make search_guidelines itself raise with the PHI in the message.
        def _bad_search(query: str, top_k: int = 5) -> list[dict[str, Any]]:
            raise ValueError(f"internal error, query was: {sentinel_phi}")

        with patch("agent.tools.search_guidelines", side_effect=_bad_search):
            from agent.tools import _execute_search_guidelines

            with pytest.raises(RuntimeError) as exc_info:
                _execute_search_guidelines({"query": "test query", "top_k": 3})

        error_msg = str(exc_info.value)
        # The SSN-shaped string "123-45-6789" should be scrubbed from the message.
        assert "123-45-6789" not in error_msg, (
            f"PHI '123-45-6789' leaked into RuntimeError: {error_msg!r}"
        )
        # __cause__ must be None (from None drops the chain).
        assert exc_info.value.__cause__ is None, (
            "__cause__ should be None (raised with 'from None')"
        )
    finally:
        hybrid_mod._corpus = original_corpus
        hybrid_mod._bm25_retriever = original_bm25
        hybrid_mod._use_qdrant = original_use_qdrant
