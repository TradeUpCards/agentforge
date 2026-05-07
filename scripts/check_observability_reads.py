#!/usr/bin/env python3
"""Find every Read tool_result for observability-security-teammate.md
to see if a longer version was ever loaded (would explain why our
recovery only got 259B — maybe there's a fuller Read earlier)."""

import json
import os
from pathlib import Path

USER_CLAUDE = Path(os.path.expanduser("~/.claude/projects"))
DIRS = [
    "C--Dev-GauntletAI-AgentForge",
    "C--Dev-GauntletAI-AgentForge-eval",
    "C--Dev-GauntletAI-AgentForge-dashboard",
    "C--Dev-GauntletAI-AgentForge-hitl",
]

target_lower = "observability-security-teammate.md"
results = []

for d in DIRS:
    base = USER_CLAUDE / d
    if not base.exists():
        continue
    for jl in sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        try:
            with jl.open(encoding="utf-8") as f:
                tool_use_idx = {}
                for line in f:
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = o.get("timestamp", "")
                    msg = o.get("message") or {}
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if not isinstance(content, list):
                        continue
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_use":
                            inp = c.get("input") or {}
                            fp = (inp.get("file_path") or "").lower().replace("/", "\\")
                            if target_lower in fp and c.get("name") == "Read":
                                tool_use_idx[c.get("id")] = (jl.name[:8], ts)
                        elif c.get("type") == "tool_result":
                            uid = c.get("tool_use_id")
                            if uid not in tool_use_idx:
                                continue
                            sess, ts2 = tool_use_idx[uid]
                            payload = c.get("content")
                            if isinstance(payload, list):
                                payload = "".join(
                                    p.get("text", "")
                                    for p in payload
                                    if isinstance(p, dict) and p.get("type") == "text"
                                )
                            elif not isinstance(payload, str):
                                payload = ""
                            results.append((ts2, sess, len(payload), payload[:200]))
        except Exception as e:
            print(f"  ! {jl.name}: {e}")

import re

def strip_lineno(text):
    return re.sub(r"^\s*\d+(?:\t|→|->)", "", text, flags=re.MULTILINE)

results.sort()
print(f"Found {len(results)} Read tool_results for observability-security-teammate.md\n")
# Dump the largest (mid-history fullest) version for inspection
results_with_full = []
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
                                tool_use_idx[c.get("id")] = (jl.name[:8], ts)
                        elif c.get("type") == "tool_result":
                            uid = c.get("tool_use_id")
                            if uid not in tool_use_idx: continue
                            sess, ts2 = tool_use_idx[uid]
                            payload = c.get("content")
                            if isinstance(payload, list):
                                payload = "".join(p.get("text","") for p in payload if isinstance(p, dict) and p.get("type")=="text")
                            elif not isinstance(payload, str):
                                payload = ""
                            results_with_full.append((ts2, sess, payload))
        except Exception: continue

results_with_full.sort()
for ts, sess, payload in results_with_full:
    stripped = strip_lineno(payload)
    print(f"=== Read at {ts} ({sess}) — {len(payload)} raw / {len(stripped)} stripped ===")
    print(stripped)
    print()
