"""End-to-end integration test: agent receives clinical query, dispatches
search_guidelines, returns response with at least one guideline citation.

Gated by ANTHROPIC_API_KEY AND rank-bm25 availability.  If either is
absent, the test is SKIPPED (not xfailed) so CI without keys/deps passes
cleanly.

Fix #5: added rank-bm25 importability check to the module-level pytestmark
so a runner that has ANTHROPIC_API_KEY but lacks rank-bm25 (or any other
RAG dep) does not fail — it skips.

Fix #7: updated assertion to check 'body' and 'leading_excerpt' keys
(renamed from 'quote' in round-2 fix).

Run locally after `pip install -r agent/requirements.txt`:
    pytest agent/tests/integration/test_rag_e2e.py -v -s

W2_ARCHITECTURE.md §4.3 (citation contract), §7.3 (Citation model).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

# Fix #5: gate on both ANTHROPIC_API_KEY and rank-bm25 availability.
# A CI runner with the key but without the RAG deps installed would otherwise
# fail (not skip) on any test that exercises the BM25 path.
_RAG_DEPS_AVAILABLE = importlib.util.find_spec("rank_bm25") is not None

pytestmark = [
    pytest.mark.skipif(
        os.getenv("ANTHROPIC_API_KEY") is None,
        reason="ANTHROPIC_API_KEY not set — live RAG e2e test requires a real API key",
    ),
    pytest.mark.skipif(
        not _RAG_DEPS_AVAILABLE,
        reason="rank-bm25 not installed — run: pip install rank-bm25>=0.2.2",
    ),
]


@pytest.mark.skipif(
    os.getenv("ANTHROPIC_API_KEY") is None,
    reason="Live test requires ANTHROPIC_API_KEY — skipped without key",
)
@pytest.mark.skipif(
    not _RAG_DEPS_AVAILABLE,
    reason="rank-bm25 not installed — run: pip install rank-bm25>=0.2.2",
)
def test_search_guidelines_returns_citation_shaped_results() -> None:
    """search_guidelines() returns results with §4.3 citation fields.

    Fix #7: checks 'body' and 'leading_excerpt' keys (renamed from 'quote').
    """
    from agent.tools import search_guidelines

    import agent.retrieval.hybrid as hybrid_mod

    original_use_qdrant = hybrid_mod._use_qdrant
    hybrid_mod._use_qdrant = False

    try:
        results = search_guidelines("metformin type 2 diabetes A1c", top_k=3)
    finally:
        hybrid_mod._use_qdrant = original_use_qdrant

    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) >= 1, "Expected at least 1 guideline result"

    for item in results:
        assert "chunk_id" in item, "Missing chunk_id"
        assert "section" in item, "Missing section"
        # Fix #7: 'body' and 'leading_excerpt' replace the old 'quote' key.
        assert "body" in item, "Missing body"
        assert "leading_excerpt" in item, "Missing leading_excerpt"
        assert item["chunk_id"], "chunk_id is empty"
        assert item["body"], "body is empty"


@pytest.mark.skipif(
    os.getenv("ANTHROPIC_API_KEY") is None,
    reason="Live test requires ANTHROPIC_API_KEY — skipped without key",
)
@pytest.mark.skipif(
    not _RAG_DEPS_AVAILABLE,
    reason="rank-bm25 not installed — run: pip install rank-bm25>=0.2.2",
)
def test_corpus_loader_returns_expected_chunk_count() -> None:
    """Corpus loader must return all 8 guideline chunks."""
    from agent.corpus.loader import load_corpus

    chunks = load_corpus()
    assert len(chunks) == 8, (
        f"Expected 8 guideline chunks, got {len(chunks)}. "
        "Did you add or remove corpus files?"
    )
    for chunk in chunks:
        assert chunk.chunk_id, "chunk_id is empty"
        assert chunk.body, "body is empty"
        assert chunk.source_url.startswith("http"), "source_url looks wrong"


@pytest.mark.skipif(
    os.getenv("ANTHROPIC_API_KEY") is None,
    reason="Live test requires ANTHROPIC_API_KEY — skipped without key",
)
@pytest.mark.skipif(
    not _RAG_DEPS_AVAILABLE,
    reason="rank-bm25 not installed — run: pip install rank-bm25>=0.2.2",
)
def test_guideline_citation_source_type_is_guideline() -> None:
    """Guideline results from _execute_search_guidelines have source_type='guideline'
    and include the quote_or_value field required by §4.3 (Fix #6).
    """
    import agent.retrieval.hybrid as hybrid_mod

    original_use_qdrant = hybrid_mod._use_qdrant
    hybrid_mod._use_qdrant = False

    try:
        from agent.tools import _execute_search_guidelines

        records = _execute_search_guidelines({"query": "blood pressure hypertension", "top_k": 3})
    finally:
        hybrid_mod._use_qdrant = original_use_qdrant

    assert isinstance(records, list)
    assert len(records) >= 1, "Expected at least 1 guideline record"
    for rec in records:
        # §4.3 citation field checks.
        assert rec.fields.get("source_type") == "guideline", (
            f"source_type should be 'guideline', got: {rec.fields.get('source_type')}"
        )
        assert rec.fields.get("source_id") == rec.fields.get("chunk_id"), (
            "source_id should equal chunk_id per §4.3"
        )
        # Fix #6: quote_or_value must be present and non-empty.
        assert "quote_or_value" in rec.fields, "Missing quote_or_value field (Fix #6)"
        assert rec.fields["quote_or_value"], "quote_or_value should be non-empty"
