"""Render the most recent eval report to HTML and open it in the default
browser. Quick demo helper — no Cursor preview required.

Usage:
    python -m agent.tests.eval.preview_latest
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

import markdown

_RESULTS = Path(__file__).parent / "results"
_HTML = _RESULTS / "_preview.html"

_CSS = """
<style>
  body { font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto;
         max-width: 920px; margin: 2rem auto; padding: 0 1.5rem;
         color: #111827; background: #f9fafb; }
  h1 { border-bottom: 2px solid #e5e7eb; padding-bottom: 0.4rem; }
  h2 { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e5e7eb; }
  h3 { margin-top: 1.6rem; color: #374151; }
  table { border-collapse: collapse; margin: 0.8rem 0; }
  th, td { border: 1px solid #d1d5db; padding: 0.4rem 0.8rem; text-align: left; }
  th { background: #e5e7eb; }
  code { background: #e5e7eb; padding: 1px 5px; border-radius: 3px;
         font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: 13px; }
  pre code { display: block; padding: 0.8rem; background: #1f2937; color: #f3f4f6; }
  ul { padding-left: 1.4rem; }
  li { margin: 0.2rem 0; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 2rem 0; }
</style>
"""


def main() -> None:
    reports = sorted(
        p for p in _RESULTS.glob("*.md") if p.name != "_preview.html"
    )
    if not reports:
        raise SystemExit(f"No eval reports found in {_RESULTS}")
    # Prefer the most recent merged "-all-modes.md" report if one exists —
    # it shows every case across live/fixture/hybrid in one view (no
    # misleading "skipped" badges from a single-mode run). Falls back to
    # the chronologically latest per-mode report otherwise.
    merged = [p for p in reports if p.name.endswith("-all-modes.md")]
    latest = merged[-1] if merged else reports[-1]
    html_body = markdown.markdown(
        latest.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code"],
    )
    page = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{latest.name}</title>{_CSS}</head>"
        f"<body><p style='color:#6b7280;'>Source: <code>{latest}</code></p>"
        f"{html_body}</body></html>"
    )
    _HTML.write_text(page, encoding="utf-8")
    print(f"Wrote: {_HTML}")
    webbrowser.open(_HTML.as_uri())


if __name__ == "__main__":
    main()
