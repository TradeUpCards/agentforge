#!/usr/bin/env python3
"""
Recover .gauntlet/ and .claude/ files from Claude Code session JSONL logs.

Context: 2026-05-07 ~00:29 a `git worktree remove --force` recursed through
the .gauntlet/ and .claude/ junctions in ../AgentForge-hitl and wiped the
canonical content in the main checkout. The Claude session JSONLs at
~/.claude/projects/ are intact — every Write / Edit / MultiEdit tool call
across all sessions is logged with full content.

This script:
  1. Walks all JSONLs across the main-project and worktree session dirs
  2. Replays Write/Edit/MultiEdit ops in chronological order
  3. Canonicalizes worktree paths to main-checkout paths (since junctions
     made them equivalent on disk)
  4. Reconstructs the latest state of every file under .gauntlet/ and .claude/
  5. Optionally writes those reconstructed files to disk

Usage:
    python scripts/recover_from_jsonl.py            # dry-run: list recoverable
    python scripts/recover_from_jsonl.py --apply    # restore files

Safety:
  - --apply will refuse to overwrite an existing non-empty file
  - Reports a manifest of what would be (or was) restored
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

USER_CLAUDE = Path(os.path.expanduser("~/.claude/projects"))

# All session dirs whose JSONLs reference files inside AgentForge.
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

# Restrict recovery to the wiped directories.
RECOVER_UNDER = [
    r"c:\dev\gauntletai\agentforge\.gauntlet",
    r"c:\dev\gauntletai\agentforge\.claude",
]


def canonicalize(raw: str) -> str:
    """Normalize a file_path string to lowercase Windows form, with all
    worktree prefixes mapped to the main checkout (since junctions made
    them equivalent on disk)."""
    if not raw:
        return ""
    p = raw.replace("/", "\\").lower().rstrip("\\")
    # Strip MSYS-style /c/... paths and convert.
    if p.startswith("\\c\\"):
        p = "c:" + p[2:]
    for wt in WORKTREE_PREFIXES:
        if p.startswith(wt + "\\"):
            return CANONICAL_PREFIX + p[len(wt):]
        if p == wt:
            return CANONICAL_PREFIX
    return p


def in_recovery_scope(canon_path: str) -> bool:
    return any(canon_path.startswith(prefix + "\\") for prefix in RECOVER_UNDER)


def iter_tool_uses(jsonl_path: Path):
    """Yield (timestamp, tool_name, input_dict) for every Write/Edit/MultiEdit
    tool_use in the JSONL. Timestamp is the message timestamp ISO string;
    falls back to file mtime + line index for older formats."""
    file_mtime = jsonl_path.stat().st_mtime
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("timestamp") or obj.get("created_at") or ""
                # Fallback: synthesize from mtime + line-idx so ordering still works
                sort_key = (ts, file_mtime, line_idx)
                msg = obj.get("message") or {}
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    name = c.get("name")
                    if name not in ("Write", "Edit", "MultiEdit"):
                        continue
                    inp = c.get("input") or {}
                    yield sort_key, name, inp
    except (OSError, UnicodeDecodeError) as e:
        print(f"  ! could not read {jsonl_path.name}: {e}", file=sys.stderr)


def collect_ops() -> List[Tuple[Tuple, str, dict]]:
    """Walk all session dirs, return all Write/Edit/MultiEdit ops sorted by
    chronological order."""
    ops = []
    for session_dir in SESSION_DIRS:
        if not session_dir.exists():
            continue
        jsonls = sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        print(f"  scanning {session_dir.name}: {len(jsonls)} JSONLs")
        for jl in jsonls:
            ops.extend(iter_tool_uses(jl))
    ops.sort(key=lambda x: x[0])
    return ops


def apply_op(state: Dict[str, str], name: str, inp: dict, stats: dict) -> None:
    """Mutate `state` (canonical_path -> content) by applying a single op."""
    raw = inp.get("file_path") or ""
    canon = canonicalize(raw)
    if not canon or not in_recovery_scope(canon):
        return

    if name == "Write":
        content = inp.get("content")
        if content is None:
            return
        state[canon] = content
        stats["writes"] = stats.get("writes", 0) + 1

    elif name == "Edit":
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        replace_all = bool(inp.get("replace_all", False))
        prior = state.get(canon)
        if prior is None or old not in prior:
            stats["edits_skipped_no_base"] = stats.get("edits_skipped_no_base", 0) + 1
            return
        if replace_all:
            state[canon] = prior.replace(old, new)
        else:
            state[canon] = prior.replace(old, new, 1)
        stats["edits_applied"] = stats.get("edits_applied", 0) + 1

    elif name == "MultiEdit":
        edits = inp.get("edits") or []
        prior = state.get(canon)
        if prior is None:
            stats["multiedits_skipped_no_base"] = stats.get("multiedits_skipped_no_base", 0) + 1
            return
        cur = prior
        for e in edits:
            old = e.get("old_string", "")
            new = e.get("new_string", "")
            replace_all = bool(e.get("replace_all", False))
            if old not in cur:
                continue
            if replace_all:
                cur = cur.replace(old, new)
            else:
                cur = cur.replace(old, new, 1)
        state[canon] = cur
        stats["multiedits_applied"] = stats.get("multiedits_applied", 0) + 1


def reconstruct() -> Tuple[Dict[str, str], dict]:
    print("=== Collecting Write/Edit/MultiEdit ops from session JSONLs ===")
    ops = collect_ops()
    print(f"  total in-scope tool ops (after filtering elsewhere): pending replay")
    state: Dict[str, str] = {}
    stats: dict = {}
    for sort_key, name, inp in ops:
        apply_op(state, name, inp, stats)
    return state, stats


def write_to_disk(state: Dict[str, str], apply: bool) -> None:
    print(f"\n=== Reconstructed {len(state)} files ===")
    if not state:
        print("  (nothing in scope — check RECOVER_UNDER paths)")
        return

    # Sort for stable output, group by top-level dir for legibility
    by_top = {}
    for canon in sorted(state):
        # Extract relative-to-main-checkout part
        rel = canon[len(CANONICAL_PREFIX):].lstrip("\\")
        top = rel.split("\\", 2)[0]
        by_top.setdefault(top, []).append((canon, rel))

    actions = {"would_write": 0, "skipped_existing": 0, "wrote": 0, "errors": 0}
    for top in sorted(by_top):
        files = by_top[top]
        total_size = sum(len(state[c]) for c, _ in files)
        print(f"\n  [{top}] {len(files)} files, {total_size:,} bytes total")
        for canon, rel in files:
            content = state[canon]
            target = MAIN_CHECKOUT / rel.replace("\\", os.sep)
            size = len(content.encode("utf-8"))
            existing = target.exists() and target.is_file() and target.stat().st_size > 0
            tag = "EXISTS-NONEMPTY-SKIP" if existing else "RESTORE"
            print(f"    {tag:22}  {size:>8} B  {rel}")

            if not apply:
                if not existing:
                    actions["would_write"] += 1
                else:
                    actions["skipped_existing"] += 1
                continue

            if existing:
                actions["skipped_existing"] += 1
                continue

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="")
                actions["wrote"] += 1
            except Exception as e:
                print(f"      ! write failed: {e}")
                actions["errors"] += 1

    print("\n=== Summary ===")
    for k, v in actions.items():
        print(f"  {k}: {v}")


def main() -> int:
    apply = "--apply" in sys.argv
    print(f"Mode: {'APPLY (will write to disk)' if apply else 'DRY-RUN (no writes)'}")
    state, stats = reconstruct()
    print(f"\n=== Replay stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    write_to_disk(state, apply)
    if not apply:
        print("\nDry-run complete. Re-run with --apply to write reconstructed files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
