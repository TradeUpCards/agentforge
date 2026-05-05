"""Corpus loader: walk guidelines/ directory → list[GuidelineChunk].

Parses YAML frontmatter (between `---` delimiters) from each .md file
and constructs GuidelineChunk objects.  The body is everything after the
closing `---` frontmatter delimiter.

Required frontmatter keys:
    chunk_id            — stable identifier (e.g. "ada-2024-s6-1-chunk-1")
    section             — section anchor (e.g. "S6.1")
    source_url          — canonical URL for the guideline
    source_attribution  — one-paragraph attribution text (plain string)

Body text (after frontmatter) must be non-empty.

W2_ARCHITECTURE.md §4.2 (corpus shape), §8.3 (no-PHI corpus posture).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_GUIDELINES_DIR = Path(__file__).parent / "guidelines"

# Frontmatter delimiter pattern — matches the opening and closing `---` lines.
_FM_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_md(path: Path) -> dict[str, Any]:
    """Parse a frontmatter+body markdown file.

    Returns a dict with keys: chunk_id, section, source_url,
    source_attribution, body.

    Raises ValueError on missing required keys or empty body.
    """
    raw = path.read_text(encoding="utf-8")
    match = _FM_PATTERN.match(raw)
    if not match:
        raise ValueError(
            f"{path.name}: Could not parse YAML frontmatter. "
            "Expected `---` delimiters at file start."
        )
    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    if not body:
        raise ValueError(f"{path.name}: Body text is empty after frontmatter.")

    # Parse frontmatter as simple key: value pairs.
    # We intentionally avoid PyYAML for this tiny format to keep the
    # corpus loader dependency-free.  Multi-line values (source_attribution)
    # use YAML block scalar syntax ("> ...") which we just strip cleanly.
    fm: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in frontmatter_text.splitlines():
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*:", line):
            # Flush previous key.
            if current_key:
                fm[current_key] = _join_value(current_lines)
            key, _, rest = line.partition(":")
            current_key = key.strip()
            current_lines = [rest.strip()]
        elif current_key and (line.startswith("  ") or line.startswith("\t") or line == ">"):
            # Continuation line for multi-line value.
            current_lines.append(line.strip().lstrip(">").strip())
        # else: blank or unrecognised — skip.

    if current_key:
        fm[current_key] = _join_value(current_lines)

    required = {"chunk_id", "section", "source_url", "source_attribution"}
    missing = required - fm.keys()
    if missing:
        raise ValueError(f"{path.name}: Missing frontmatter keys: {missing!r}")

    return {
        "chunk_id": fm["chunk_id"],
        "section": fm["section"],
        "source_url": fm["source_url"],
        "source_attribution": fm["source_attribution"],
        "body": body,
    }


def _join_value(lines: list[str]) -> str:
    """Join multi-line YAML value parts into a single string."""
    return " ".join(part for part in lines if part)


def load_corpus(guidelines_dir: Path | None = None) -> list[Any]:
    """Walk the guidelines directory and return list[GuidelineChunk].

    Args:
        guidelines_dir: Override for the default guidelines/ path.
                        Useful in tests that want a temp directory.

    Returns:
        List of GuidelineChunk objects sorted by chunk_id for reproducibility.

    Raises:
        FileNotFoundError: if the guidelines directory does not exist.
        ValueError:        if any .md file fails to parse (fails fast).
    """
    from agent.document_schemas import GuidelineChunk  # noqa: PLC0415

    target_dir = guidelines_dir or _GUIDELINES_DIR
    if not target_dir.exists():
        raise FileNotFoundError(
            f"Guidelines directory not found: {target_dir}. "
            "Did you run the corpus ingest step?"
        )

    md_files = sorted(target_dir.glob("*.md"))
    if not md_files:
        _logger.warning("No .md files found in %s", target_dir)
        return []

    chunks: list[Any] = []
    for path in md_files:
        try:
            data = _parse_md(path)
            chunks.append(
                GuidelineChunk(
                    chunk_id=data["chunk_id"],
                    section=data["section"],
                    source_url=data["source_url"],
                    source_attribution=data["source_attribution"],
                    body=data["body"],
                )
            )
        except Exception as exc:
            _logger.error("Failed to parse %s: %s", path.name, exc)
            raise

    _logger.info("Loaded %d guideline chunks from %s.", len(chunks), target_dir)
    return chunks
