"""Hybrid RAG retrieval package for Stage-3a.

Exposes the single public entry-point used by the search_guidelines tool:

    from agent.retrieval.hybrid import hybrid_search

Pipeline: BM25 top-20 + vector top-20 → union (dedup by chunk_id) →
Cohere Rerank (or BAAI fallback) → top-k.

W2_ARCHITECTURE.md §4.1, §6.3.
"""
