"""Clinical guideline corpus package for Stage-3a hybrid RAG.

Contains:
    agent/corpus/guidelines/   — markdown files (frontmatter + body)
    agent/corpus/loader.py     — walks guidelines/, returns list[GuidelineChunk]
    agent/corpus/ingest.py     — one-shot CLI: load → embed → upsert into Qdrant

All corpus text is public-domain clinical guidance (USPSTF, ADA, JNC8/ACC-AHA
paraphrased summaries).  No PHI by construction.

W2_ARCHITECTURE.md §4.2, §8.2 (no-PHI corpus posture).
"""
