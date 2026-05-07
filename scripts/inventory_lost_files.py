#!/usr/bin/env python3
"""Inventory every file in a target subtree (.gauntlet/ or .claude/) that
appears in any JSONL tool op, comparing JSONL refs to current disk state.
Reports MISSING entries (in JSONL but not on disk) and what tools touched them.

Usage:  python scripts/inventory_lost_files.py [--subtree .gauntlet|.claude]
"""

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
PREFIX_RE = re.compile(r"c:[\\/]+dev[\\/]+gauntletai[\\/]+agentforge(-[a-z]+)?", re.IGNORECASE)


def main():
    subtree = ".gauntlet"
    for i, a in enumerate(sys.argv):
        if a == "--subtree" and i + 1 < len(sys.argv):
            subtree = sys.argv[i + 1]
            if not subtree.startswith("."):
                subtree = "." + subtree

    # Hardcode the two real cases — dynamic regex assembly was buggy.
    if subtree == ".gauntlet":
        pat = re.compile(
            r"c:[\\/]+dev[\\/]+gauntletai[\\/]+agentforge(-[a-z]+)?[\\/]+\.gauntlet[\\/]+",
            re.IGNORECASE,
        )
    elif subtree == ".claude":
        pat = re.compile(
            r"c:[\\/]+dev[\\/]+gauntletai[\\/]+agentforge(-[a-z]+)?[\\/]+\.claude[\\/]+",
            re.IGNORECASE,
        )
    else:
        print(f"Unsupported subtree: {subtree}", file=sys.stderr)
        return 2

    canonical_prefix = r"c:\dev\gauntletai\agentforge"

    seen = {}
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
                            normalized = fp.replace("/", "\\")
                            if not pat.search(normalized):
                                continue
                            # Use a callable replacement to avoid re.sub
                            # interpreting backslash-letter sequences in
                            # canonical_prefix (\d, \g, \a) as escapes.
                            canon = PREFIX_RE.sub(
                                lambda m: canonical_prefix, normalized.lower()
                            )
                            seen.setdefault(canon, set()).add(c.get("name"))
            except Exception as e:
                print(f"  ! {jl.name}: {e}", file=sys.stderr)

    print(f"Subtree: {subtree}")
    print(f"Found {len(seen)} unique paths in JSONLs.\n")

    ok = miss = 0
    miss_list = []
    for canon in sorted(seen):
        rel = canon[len(canonical_prefix):].lstrip("\\")
        target = MAIN_CHECKOUT / rel.replace("\\", os.sep)
        on_disk = target.exists() and target.is_file() and target.stat().st_size > 0
        if on_disk:
            ok += 1
        else:
            miss += 1
            miss_list.append((canon, rel, sorted(seen[canon])))

    print(f"On disk:  {ok}")
    print(f"MISSING:  {miss}")
    if miss_list:
        print()
        for canon, rel, tools in miss_list:
            print(f"  MISS  ({','.join(tools):<22})  {rel}")


if __name__ == "__main__":
    main()
