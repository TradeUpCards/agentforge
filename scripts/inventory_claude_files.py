#!/usr/bin/env python3
"""Discover every .claude/* file mentioned in any tool op across JSONLs.
Show which are on disk vs missing."""

import json
import os
import re
import sys
from pathlib import Path

USER_CLAUDE = Path(os.path.expanduser("~/.claude/projects"))
DIRS = [
    "C--Dev-GauntletAI-AgentForge",
    "C--Dev-GauntletAI-AgentForge-eval",
    "C--Dev-GauntletAI-AgentForge-dashboard",
    "C--Dev-GauntletAI-AgentForge-hitl",
]
MAIN_CHECKOUT = Path(r"C:\Dev\GauntletAI\AgentForge")

# Match any .claude/ path under any worktree variant
WT_PAT = re.compile(r"c:[\\/]+dev[\\/]+gauntletai[\\/]+agentforge(-[a-z]+)?[\\/]+\.claude[\\/]+", re.IGNORECASE)
PREFIX_RE = re.compile(r"c:[\\/]+dev[\\/]+gauntletai[\\/]+agentforge(-[a-z]+)?", re.IGNORECASE)


def canonicalize(raw: str) -> str:
    if not raw:
        return ""
    p = raw.replace("/", "\\").lower()
    return PREFIX_RE.sub(r"c:\\dev\\gauntletai\\agentforge", p)


def main():
    seen = {}  # canon_path -> set of tool names
    for d in DIRS:
        base = USER_CLAUDE / d
        if not base.exists():
            continue
        for jl in base.glob("*.jsonl"):
            try:
                with jl.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            o = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = o.get("message") or {}
                        content = msg.get("content") if isinstance(msg, dict) else None
                        if not isinstance(content, list):
                            continue
                        for c in content:
                            if not isinstance(c, dict) or c.get("type") != "tool_use":
                                continue
                            inp = c.get("input") or {}
                            fp = inp.get("file_path") or ""
                            if not WT_PAT.search(fp.replace("/", "\\")):
                                continue
                            canon = canonicalize(fp)
                            seen.setdefault(canon, set()).add(c.get("name"))
            except Exception as e:
                print(f"  ! {jl.name}: {e}", file=sys.stderr)

    if not seen:
        print("  (no .claude/* file refs found)")
        return

    base_canon = r"c:\dev\gauntletai\agentforge"
    print(f"Found {len(seen)} unique .claude/* paths in JSONLs:\n")
    for canon in sorted(seen):
        rel = canon[len(base_canon):].lstrip("\\")
        target = MAIN_CHECKOUT / rel.replace("\\", os.sep)
        on_disk = target.exists() and target.is_file() and target.stat().st_size > 0
        flag = "OK   " if on_disk else "MISS "
        size = f"{target.stat().st_size:>5}B" if on_disk else "  -- "
        tools = ",".join(sorted(seen[canon]))
        print(f"  [{flag}] {size}  ({tools:<22})  {rel}")


if __name__ == "__main__":
    main()
