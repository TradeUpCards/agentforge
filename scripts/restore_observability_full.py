#!/usr/bin/env python3
"""Restore the full (1486-char stripped) version of
observability-security-teammate.md from the May-5 14:29 Read tool_result.
The current 259-byte recovery is malformed (frontmatter not closed).
"""

import json
import os
import re
from pathlib import Path

USER_CLAUDE = Path(os.path.expanduser("~/.claude/projects"))
DIRS = [
    "C--Dev-GauntletAI-AgentForge",
    "C--Dev-GauntletAI-AgentForge-eval",
    "C--Dev-GauntletAI-AgentForge-dashboard",
    "C--Dev-GauntletAI-AgentForge-hitl",
]
target_lower = "observability-security-teammate.md"
target_path = Path(r"C:\Dev\GauntletAI\AgentForge\.claude\agents\observability-security-teammate.md")

LINENO = re.compile(r"^\s*\d+(?:\t|→|->)", re.MULTILINE)

candidates = []
for d in DIRS:
    base = USER_CLAUDE / d
    if not base.exists():
        continue
    for jl in sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        try:
            with jl.open(encoding="utf-8") as f:
                tool_use_idx = {}
                for line in f:
                    try: o = json.loads(line)
                    except: continue
                    ts = o.get("timestamp", "")
                    msg = o.get("message") or {}
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if not isinstance(content, list): continue
                    for c in content:
                        if not isinstance(c, dict): continue
                        if c.get("type") == "tool_use":
                            inp = c.get("input") or {}
                            fp = (inp.get("file_path") or "").lower().replace("/", "\\")
                            if target_lower in fp and c.get("name") == "Read":
                                tool_use_idx[c.get("id")] = ts
                        elif c.get("type") == "tool_result":
                            uid = c.get("tool_use_id")
                            if uid not in tool_use_idx: continue
                            payload = c.get("content")
                            if isinstance(payload, list):
                                payload = "".join(p.get("text","") for p in payload if isinstance(p, dict) and p.get("type")=="text")
                            elif not isinstance(payload, str):
                                payload = ""
                            stripped = LINENO.sub("", payload)
                            candidates.append((tool_use_idx[uid], len(stripped), stripped))
        except: continue

# Pick the LARGEST stripped version (the one with full body)
candidates.sort(key=lambda x: x[1], reverse=True)
ts, size, content = candidates[0]
print(f"Picked Read at {ts} — {size} chars (largest of {len(candidates)} candidates)")
print(f"Writing {target_path}")
target_path.write_text(content, encoding="utf-8", newline="")
print(f"Done. New size on disk: {target_path.stat().st_size} bytes")
