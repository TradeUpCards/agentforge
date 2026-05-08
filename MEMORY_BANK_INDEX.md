# Memory Bank Index

> AgentForge uses **domain-meaningful filenames** instead of the canonical
> Memory Bank file names. The structural pattern is the same — this index
> documents the mapping so anyone arriving with Memory Bank conventions can
> orient quickly.
>
> Decision rationale: the existing names (`USERS.md`, `EVAL_SUITE.md`, `AUDIT.md`)
> are more discoverable for this project's audience (graders, hospital CTOs,
> prospective employers in clinical-AI) than the canonical names. Cross-link
> stability across the cheat sheet, video script, slide deck, and the
> session handoff was prioritized over naming-convention compliance.

---

## Canonical mapping

```
                 projectbrief
        ┌────────────┴────────────┐
        │                          │
   README.md            .gauntlet/weekN/prd.md
   (overall pitch)      (per-week scope)
                        │
                        ↓
       ┌────────────────┼────────────────┐
       ↓                ↓                ↓
 productContext   systemPatterns     techContext
       │                │                │
   USERS.md       ARCHITECTURE.md     CLAUDE.md
                  W2_ARCHITECTURE.md
                        │
                        ↓
                  activeContext  ──→  progress
                        │                │
                CLAUDE_SESSION_HANDOFF.md (same file — sections within)
```

| Memory Bank canonical | AgentForge file(s) | What it covers |
|---|---|---|
| `projectbrief.md` | `README.md` + `.gauntlet/weekN/prd.md` | Overall pitch + per-week scope |
| `productContext.md` | `USERS.md` | Target user, workflow, use cases, persona-vs-alternatives reasoning |
| `systemPatterns.md` | `ARCHITECTURE.md`, `W2_ARCHITECTURE.md` | System architecture, design patterns, key decisions |
| `techContext.md` | `CLAUDE.md` | Tech stack, build, test, code quality, PSR standards |
| `activeContext.md` | `CLAUDE_SESSION_HANDOFF.md` | Current objective, decisions this session, files touched, blockers, next-PM-prompt |
| `progress.md` | `CLAUDE_SESSION_HANDOFF.md` (same file) | What works, what's gated, deferrals — sections within the handoff |

## What AgentForge has that Memory Bank doesn't

These docs go beyond the canonical pattern. They earn their own files because each one answers a question the audience explicitly asks:

| File | What it adds | Audience |
|---|---|---|
| `DECISIONS.md` | Decision log with rationale, options considered, cross-links | Reviewers, future maintainers |
| `AUDIT.md` | Security findings, threat model, mitigations | Hospital CTO, security reviewer |
| `COST_ANALYSIS.md` | Economics across user-count tiers, sensitivity analysis | CFO-shaped questions, scaling defense |
| `EVAL_SUITE.md` | Eval framework, categories, regression-gate design | Grader, ML/eng reviewer |
| `~/.claude/projects/<mangled>/memory/feedback_*.md` | Disciplines auto-loaded every session (story-capture, session-summary patterns, etc.) | Future Claude sessions inheriting context |

## Where to look for what

| If you're asking... | Read first |
|---|---|
| What is this project? | `README.md` |
| Who is it for and what do they need? | `USERS.md` |
| How is it built? | `ARCHITECTURE.md` then `W2_ARCHITECTURE.md` |
| What's the current week's scope? | `.gauntlet/weekN/prd.md` |
| What's the current state of the build? | `CLAUDE_SESSION_HANDOFF.md` |
| Why did you choose X over Y? | `DECISIONS.md` |
| Is this safe / how does it fail? | `AUDIT.md` |
| What does this cost at scale? | `COST_ANALYSIS.md` |
| How do you catch regressions? | `EVAL_SUITE.md` |
| How do I run / build / test? | `CLAUDE.md` |
| What gets remembered across Claude sessions? | `~/.claude/projects/<mangled>/memory/MEMORY.md` |

## Auto-loading behavior

The Claude Code session loads:

1. `CLAUDE.md` (techContext) — every session, automatic
2. `~/.claude/projects/<mangled-path>/memory/MEMORY.md` (the feedback / state index) — every session, automatic
3. The session-handoff file (`CLAUDE_SESSION_HANDOFF.md`) — read at start when present
4. Other docs (`USERS.md`, `ARCHITECTURE.md`, etc.) — read on demand by the agent when relevant

This is functionally equivalent to Memory Bank's "read all memory-bank files on session start" pattern. The difference is that AgentForge reads on demand rather than eagerly, which keeps context utilization lower.

## When to revisit this decision

Reorganization is warranted IF:
- A future project starts clean (no existing cross-link debt)
- A grader / employer requires Memory-Bank-compliant naming explicitly
- The team grows beyond solo development and the canonical names reduce onboarding friction

Until any of those triggers, the current naming wins on cost-benefit. This index is the bridge.
