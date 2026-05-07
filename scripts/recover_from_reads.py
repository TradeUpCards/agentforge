#!/usr/bin/env python3
"""
Phase 2 recovery: reconstruct files from Read tool_results when Write was
never called. Read tool_results echo the file's content at read-time. We
strip the cat -n style line-number prefix and use that as a base, then
apply any Edit/MultiEdit ops that came chronologically AFTER the most
recent Read.

Targets the 8 team agent files missing from phase-1 recovery:
  .claude/agents/{gauntlet-team-lead, agent-rag-teammate,
                  backend-openemr-teammate, delivery-lead,
                  observability-security-teammate,
                  product-architecture-lead, quality-lead,
                  ui-ux-teammate}.md
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

USER_CLAUDE = Path(os.path.expanduser("~/.claude/projects"))
SESSION_DIRS = [
    USER_CLAUDE / "C--Dev-GauntletAI-AgentForge",
    USER_CLAUDE / "C--Dev-GauntletAI-AgentForge-eval",
    USER_CLAUDE / "C--Dev-GauntletAI-AgentForge-dashboard",
    USER_CLAUDE / "C--Dev-GauntletAI-AgentForge-hitl",
]
MAIN_CHECKOUT = Path(r"C:\Dev\GauntletAI\AgentForge")
WORKTREE_PREFIXES = [
    r"c:\dev\gauntletai\agentforge-eval",
    r"c:\dev\gauntletai\agentforge-dashboard",
    r"c:\dev\gauntletai\agentforge-hitl",
    r"c:\dev\gauntletai\agentforge-cicd",
    r"c:\dev\gauntletai\agentforge-slo",
    r"c:\dev\gauntletai\agentforge-launcher",
]
CANONICAL_PREFIX = r"c:\dev\gauntletai\agentforge"

# Targeted missing files. Phase-2a was the 8 team agents; phase-2b adds
# implementation-lead + the two settings.json files.
TARGETS = {
    r"c:\dev\gauntletai\agentforge\.claude\agents\gauntlet-team-lead.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\agent-rag-teammate.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\backend-openemr-teammate.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\delivery-lead.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\observability-security-teammate.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\product-architecture-lead.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\quality-lead.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\ui-ux-teammate.md",
    r"c:\dev\gauntletai\agentforge\.claude\agents\implementation-lead.md",
    r"c:\dev\gauntletai\agentforge\.claude\settings.json",
    r"c:\dev\gauntletai\agentforge\.claude\settings.local.json",
}

# Read tool's line-number prefix. Observed forms across Claude Code versions:
#   "   123→content"   (U+2192 right-arrow)
#   "  123\tcontent"   (tab)
#   "  123->content"   (ASCII arrow, rare)
# Matches optional leading whitespace + digits + one of those delimiters.
LINENO_PREFIX = re.compile(r"^\s*\d+(?:\t|→|->)", re.MULTILINE)


def canonicalize(raw: str) -> str:
    if not raw:
        return ""
    p = raw.replace("/", "\\").lower().rstrip("\\")
    if p.startswith("\\c\\"):
        p = "c:" + p[2:]
    for wt in WORKTREE_PREFIXES:
        if p.startswith(wt + "\\"):
            return CANONICAL_PREFIX + p[len(wt):]
        if p == wt:
            return CANONICAL_PREFIX
    return p


def strip_line_numbers(text: str) -> str:
    """Remove cat -n style line-number prefixes from Read tool_result output."""
    lines = text.splitlines(keepends=True)
    out = []
    pat = re.compile(r"^\s*\d+(?:\t|→|->)")
    for line in lines:
        out.append(pat.sub("", line, count=1))
    return "".join(out)


def extract_tool_result_content(result_obj) -> Optional[str]:
    """Extract text content from a tool_result entry. The content can be a
    string or a list of {type:'text', text:'...'} blocks."""
    if isinstance(result_obj, str):
        return result_obj
    if isinstance(result_obj, list):
        parts = []
        for item in result_obj:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts) if parts else None
    return None


def collect_per_file_history():
    """For each TARGET file, collect chronologically:
        - all (timestamp, 'read', content) where content is from Read tool_result
        - all (timestamp, 'write', content) where content is from Write tool_use
        - all (timestamp, 'edit', (old, new, replace_all)) from Edit/MultiEdit
        Then, latest base wins, then apply later Edits.
    """
    # Per-file ordered events: list of (ts, kind, payload)
    per_file: Dict[str, List[Tuple]] = {tgt: [] for tgt in TARGETS}

    # First pass: index tool_use entries by their id, so we can look up
    # the file_path/name when we see a tool_result with that id.
    # Then second pass to collect content.
    for session_dir in SESSION_DIRS:
        if not session_dir.exists():
            continue
        for jl in sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
            mtime = jl.stat().st_mtime
            tool_use_index: Dict[str, Tuple[str, dict, tuple]] = {}
            # Walk the file once, track use_ids, capture results when seen
            try:
                with jl.open(encoding="utf-8") as f:
                    for line_idx, line in enumerate(f):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = o.get("timestamp") or o.get("created_at") or ""
                        sort_key = (ts, mtime, line_idx)
                        msg = o.get("message") or {}
                        content = msg.get("content") if isinstance(msg, dict) else None
                        if not isinstance(content, list):
                            continue
                        for c in content:
                            if not isinstance(c, dict):
                                continue
                            ctype = c.get("type")
                            if ctype == "tool_use":
                                name = c.get("name")
                                inp = c.get("input") or {}
                                use_id = c.get("id")
                                fp_canon = canonicalize(inp.get("file_path") or "")
                                if fp_canon not in TARGETS:
                                    continue
                                if name == "Read":
                                    tool_use_index[use_id] = (name, inp, sort_key)
                                elif name == "Write":
                                    payload = inp.get("content") or ""
                                    per_file[fp_canon].append((sort_key, "write", payload))
                                elif name == "Edit":
                                    payload = (
                                        inp.get("old_string", ""),
                                        inp.get("new_string", ""),
                                        bool(inp.get("replace_all", False)),
                                    )
                                    per_file[fp_canon].append((sort_key, "edit", payload))
                                elif name == "MultiEdit":
                                    edits = inp.get("edits") or []
                                    per_file[fp_canon].append((sort_key, "multiedit", edits))
                            elif ctype == "tool_result":
                                use_id = c.get("tool_use_id")
                                if use_id not in tool_use_index:
                                    continue
                                name, inp, used_sort = tool_use_index[use_id]
                                if name != "Read":
                                    continue
                                fp_canon = canonicalize(inp.get("file_path") or "")
                                if fp_canon not in TARGETS:
                                    continue
                                content_payload = extract_tool_result_content(c.get("content"))
                                if content_payload is None:
                                    continue
                                # Use the use's sort_key so reads land in chrono order
                                per_file[fp_canon].append((used_sort, "read", content_payload))
            except Exception as e:
                print(f"  ! could not read {jl.name}: {e}", file=sys.stderr)

    # Sort per-file events
    for fp in per_file:
        per_file[fp].sort(key=lambda x: x[0])
    return per_file


def reconstruct_file(events: List[Tuple]) -> Optional[str]:
    """Replay events to derive latest content. Use the most recent Read or
    Write as the base, then apply later Edit/MultiEdit ops."""
    # Find the most recent Write or Read; that's our base
    base_idx = None
    base_content = None
    base_kind = None
    for i, (ts, kind, payload) in enumerate(events):
        if kind in ("write", "read"):
            base_idx = i
            base_content = payload if kind == "write" else strip_line_numbers(payload)
            base_kind = kind
    if base_idx is None:
        return None

    cur = base_content
    for ts, kind, payload in events[base_idx + 1:]:
        if kind == "edit":
            old, new, replace_all = payload
            if old not in cur:
                continue
            cur = cur.replace(old, new) if replace_all else cur.replace(old, new, 1)
        elif kind == "multiedit":
            for e in payload:
                old = e.get("old_string", "")
                new = e.get("new_string", "")
                replace_all = bool(e.get("replace_all", False))
                if old not in cur:
                    continue
                cur = cur.replace(old, new) if replace_all else cur.replace(old, new, 1)
    return cur


def main() -> int:
    apply = "--apply" in sys.argv
    print(f"Mode: {'APPLY (will write to disk)' if apply else 'DRY-RUN (no writes)'}")
    print(f"Targets: {len(TARGETS)} files\n")

    per_file = collect_per_file_history()

    summary = {"reconstructed": 0, "no_data": 0, "wrote": 0, "skipped_existing": 0, "errors": 0}
    for canon, events in per_file.items():
        rel = canon[len(CANONICAL_PREFIX):].lstrip("\\").replace("\\", os.sep)
        target_path = MAIN_CHECKOUT / rel
        if not events:
            print(f"  NO DATA  {rel}")
            summary["no_data"] += 1
            continue
        content = reconstruct_file(events)
        if content is None:
            print(f"  NO BASE  {rel}  (events: {len(events)}, all edits, no read/write)")
            summary["no_data"] += 1
            continue
        size = len(content.encode("utf-8"))
        existing = target_path.exists() and target_path.is_file() and target_path.stat().st_size > 0
        kinds = ", ".join(sorted({e[1] for e in events}))
        tag = "EXISTS-SKIP" if existing else "RESTORE"
        print(f"  {tag:12}  {size:>6} B  {rel}  (events: {len(events)} of [{kinds}])")
        summary["reconstructed"] += 1
        if not apply:
            continue
        if existing:
            summary["skipped_existing"] += 1
            continue
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8", newline="")
            summary["wrote"] += 1
        except Exception as e:
            print(f"    ! write failed: {e}")
            summary["errors"] += 1

    print(f"\n=== Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if not apply:
        print("\nDry-run complete. Re-run with --apply to write reconstructed files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
