#!/usr/bin/env python3
"""Render and transition lead kickoff prompts via a YAML config.

The kickoff format historically lived in a hand-authored markdown file at
``.gauntlet/week2/kickoff/<lead>.md``. As the workflow matured, the same
structure recurred per phase (P1, P2, P3 ...) with only a few fields rotating
each time: branch, worktree path, mission summary, and the per-session "first
task" pointer. The lead-invariant content (identity, operating discipline, file
ownership) stayed stable across phases.

This module promotes that pattern into a structured YAML config and a render
step. The launcher reads the rendered ``<lead>.md`` exactly as before; the
YAML is a layer underneath that drives generation.

Workflow:

* During a session, the lead writes their handoff at
  ``.gauntlet/week2/handoffs/<lead>-handoff.md`` including a section
  ``## Recommended next PM prompt`` containing a fenced code block with the
  next session's "first task" instructions, and a section
  ``## Recommended next agent-team formation`` listing the recommended
  teammates.

* At phase transition, ``finish_<lead> --next-phase P3 --next-branch X`` runs
  ``render_kickoff.py transition <lead> ...`` which:

    1. Reads the handoff and extracts the recommended-prompt body and team.
    2. Marks the current phase ``complete`` in ``<lead>.yml``.
    3. Adds (or updates) the new phase's entry with the new branch / worktree /
       extracted body / extracted team.
    4. Sets ``current_phase`` to the new phase name.
    5. Re-renders ``<lead>.md`` from the active phase + lead-invariant fields.

* On next launch, ``start_<lead>`` re-renders ``<lead>.md`` and Claude reads
  the rendered prompt with the new phase's content.

Subcommands:

    render <lead>
        Read ``<lead>.yml``, render to ``<lead>.md`` from the active phase.

    transition <lead> --to <phase> --branch <name> [--worktree <path>]
                      --from-handoff <path>
                      [--current-phase-name <name>]
        Promote handoff content into a new phase, mark previous complete.

    extract-prompt <handoff-path>
        Print the first fenced code block under ``## Recommended next PM prompt``
        to stdout. Diagnostic / scripting helper.

    extract-team <handoff-path>
        Print the bullet list under ``## Recommended next agent-team formation``
        as one teammate per line. Diagnostic / scripting helper.

    init-from-md <lead> --current-phase-name <name>
        Bootstrap a YAML from an existing hand-authored ``<lead>.md`` and the
        current handoff. Used to migrate a lead from the legacy format to YAML
        without losing content.

The script depends on PyYAML. It's intentionally not a class hierarchy or a
library import; the launcher invokes it as a CLI. Each subcommand returns a
non-zero exit code on failure with a structured error on stderr.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "render_kickoff.py: PyYAML is not installed. Install with:\n"
        "  pip install pyyaml\n"
    )
    sys.exit(2)


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------


def _kickoff_dir(repo_root: Path) -> Path:
    return repo_root / ".gauntlet" / "week2" / "kickoff"


def _handoffs_dir(repo_root: Path) -> Path:
    return repo_root / ".gauntlet" / "week2" / "handoffs"


def _yml_path(repo_root: Path, lead: str) -> Path:
    return _kickoff_dir(repo_root) / f"{lead}.yml"


def _md_path(repo_root: Path, lead: str) -> Path:
    return _kickoff_dir(repo_root) / f"{lead}.md"


def _handoff_path(repo_root: Path, lead: str) -> Path:
    return _handoffs_dir(repo_root) / f"{lead}-handoff.md"


def _resolve_repo_root() -> Path:
    """Find the repo root that owns the canonical ``.gauntlet/`` directory.

    Resolution order (matches the bash launcher's ``AGENTFORGE_ROOT`` convention):

    1. ``AGENTFORGE_ROOT`` environment variable, if set and the path exists.
    2. The script's own parent's parent (``<repo>/scripts/render_kickoff.py``
       → ``<repo>``). This is the natural answer when the script is invoked
       from the main checkout.

    The bash launcher exports ``AGENTFORGE_ROOT`` so a sourced shell can
    override it. We honor the same variable so callers from any worktree
    (where this script may live in the linked tree, but ``.gauntlet/`` lives
    in the main checkout) get the right root.
    """
    env_root = os.environ.get("AGENTFORGE_ROOT")
    if env_root:
        p = Path(env_root)
        if p.exists():
            return p.resolve()
    here = Path(__file__).resolve()
    return here.parent.parent


# ----------------------------------------------------------------------------
# YAML schema
# ----------------------------------------------------------------------------
#
# {
#   "lead": "aria",                       # required, str
#   "workstream": "HITL extraction (...)", # required, str
#   "file_ownership": ["agent/.../**", ...],   # optional, list of glob strings
#   "shared_files": [                          # optional, list of {path,note}
#     {"path": "agent/main.py", "note": "coordinate with Bram"}
#   ],
#   "operating_discipline": "..." | ["...", ...],   # optional, str or list
#   "inherited_section": "..." | None,              # optional, str
#   "current_phase": "P3",                # required, str — must be a key in phases
#   "phases": {
#     "P1": {
#       "branch": "agentforge/w2-...",    # required
#       "worktree": "../AgentForge-hitl", # required
#       "status": "complete",             # not_started | active | complete | abandoned
#       "mission": "P1 — eval metrics",   # required, short summary line
#       "first_task": "Ship P1 ...",      # required, multi-line ok
#       "team": ["agent-rag-teammate", ...],  # optional
#     },
#     ...
#   }
# }


_REQUIRED_TOP = {"lead", "workstream", "current_phase", "phases"}
_REQUIRED_PHASE = {"branch", "worktree", "status", "mission", "first_task"}
_VALID_STATUSES = {"not_started", "active", "complete", "abandoned"}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"render_kickoff: yaml not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"render_kickoff: {path} did not parse as a mapping")
    return data


def _save_yaml(path: Path, data: dict) -> None:
    # default_flow_style=False forces block style (readable for humans).
    # sort_keys=False preserves insertion order so phases stay in P1, P2, P3.
    # width=10**9 prevents PyYAML from wrapping long mission/first_task lines.
    out = yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10**9,
    )
    with path.open("w", encoding="utf-8") as f:
        f.write(out)


def _validate(data: dict, source: str) -> None:
    """Raise SystemExit with a clear message if the YAML is malformed."""
    missing = _REQUIRED_TOP - set(data.keys())
    if missing:
        raise SystemExit(
            f"render_kickoff: {source} is missing required top-level keys: {sorted(missing)}"
        )
    if not isinstance(data["phases"], dict) or not data["phases"]:
        raise SystemExit(f"render_kickoff: {source} 'phases' must be a non-empty mapping")
    cur = data["current_phase"]
    if cur not in data["phases"]:
        raise SystemExit(
            f"render_kickoff: {source} current_phase={cur!r} but phases has "
            f"only {sorted(data['phases'].keys())}"
        )
    for name, phase in data["phases"].items():
        if not isinstance(phase, dict):
            raise SystemExit(f"render_kickoff: {source} phase {name} must be a mapping")
        miss = _REQUIRED_PHASE - set(phase.keys())
        if miss:
            raise SystemExit(
                f"render_kickoff: {source} phase {name} missing required keys: {sorted(miss)}"
            )
        if phase["status"] not in _VALID_STATUSES:
            raise SystemExit(
                f"render_kickoff: {source} phase {name} status={phase['status']!r} "
                f"is not one of {sorted(_VALID_STATUSES)}"
            )


# ----------------------------------------------------------------------------
# Render YAML → markdown
# ----------------------------------------------------------------------------


def _format_bullets(items, indent: str = "- ") -> str:
    """Format a list of strings as a bullet list. Each item on its own line."""
    return "\n".join(f"{indent}{item}" for item in items)


_GENERATED_HEADER = (
    "<!-- GENERATED FROM kickoff/{lead}.yml by scripts/render_kickoff.py — DO NOT EDIT.\n"
    "     Edit the .yml instead, then run `python scripts/render_kickoff.py render {lead}`,\n"
    "     or transition phases via `finish_{lead} --next-phase ...`. -->"
)


def _render(data: dict) -> str:
    """Produce the markdown body the launcher will parse and Claude will read."""
    lead = data["lead"]
    lead_title = lead[:1].upper() + lead[1:].lower()
    workstream = data["workstream"]
    cur_name = data["current_phase"]
    cur = data["phases"][cur_name]

    parts: list[str] = []

    # GENERATED-from-YAML header so hand edits don't get silently clobbered
    # on the next render step.
    parts.append(_GENERATED_HEADER.format(lead=lead))
    parts.append("")

    # Identity + read-order block. The line that mentions in-flight is the
    # earliest reading direction; keep it first so Claude's first action
    # always honors the cross-lead coordination contract.
    parts.append(
        f"You are **{lead_title}**, the lead for the **{workstream}** workstream "
        f"on Week 2 (current phase: **{cur_name}** — {cur['mission']})."
    )
    parts.append("")
    parts.append("Before any edits, read in this order:")
    parts.append("")
    parts.append("1. `.gauntlet/week2/in-flight.md` — workstream rules, file ownership, memory discipline")
    parts.append(f"2. `.gauntlet/week2/handoffs/{lead}-handoff.md` — your prior session state (may not exist yet on first run)")
    parts.append(f"3. `.gauntlet/week2/kickoff/{lead}.yml` — phase config (this kickoff is rendered from it)")
    parts.append("4. `CLAUDE.md` — project conventions")
    parts.append("")

    # Workstream + branch + worktree — the launcher's parser anchors on
    # these `**Branch:**` / `**Worktree:**` lines.
    parts.append(f"**Workstream:** `{workstream}`")
    parts.append(f"**Branch:** `{cur['branch']}`")
    parts.append(f"**Worktree:** `{cur['worktree']}`")
    parts.append("")

    # File ownership.
    file_ownership = data.get("file_ownership") or []
    if file_ownership:
        parts.append("**You own** (no other lead edits these):")
        parts.append(_format_bullets([f"`{f}`" for f in file_ownership]))
        parts.append("")

    # Shared files (one per line, with optional notes).
    shared = data.get("shared_files") or []
    if shared:
        if len(shared) == 1:
            entry = shared[0]
            note = entry.get("note", "").strip()
            note_part = f" ({note})" if note else ""
            parts.append(f"**Shared file**{note_part}: `{entry['path']}`")
        else:
            parts.append("**Shared files** (coordinate before editing):")
            for entry in shared:
                note = entry.get("note", "").strip()
                note_part = f" — {note}" if note else ""
                parts.append(f"- `{entry['path']}`{note_part}")
        parts.append("")

    # Operating discipline. Either a multi-line string or a list of bullets.
    discipline = data.get("operating_discipline")
    if discipline:
        parts.append("**Operating discipline:**")
        if isinstance(discipline, list):
            parts.append(_format_bullets(discipline))
        else:
            parts.append(str(discipline).rstrip())
        parts.append("")

    # Inherited section (e.g., "Inherited from gauntlet-team-lead — non-negotiable").
    inherited = data.get("inherited_section")
    if inherited:
        parts.append(str(inherited).rstrip())
        parts.append("")

    # Phase history — short table for context. Optional but useful so the
    # current session knows what previous phases shipped.
    phase_history = []
    for name, phase in data["phases"].items():
        if name == cur_name:
            continue
        if phase["status"] == "complete":
            phase_history.append(f"- **{name}** — {phase['mission']} (complete)")
        elif phase["status"] == "abandoned":
            phase_history.append(f"- **{name}** — {phase['mission']} (abandoned)")
    if phase_history:
        parts.append("**Phase history:**")
        parts.append("\n".join(phase_history))
        parts.append("")

    # Current phase first_task — what to do this session.
    parts.append(f"**Your first task ({cur_name}):**")
    parts.append("")
    parts.append(cur["first_task"].rstrip())
    parts.append("")

    # Recommended team for this phase, if specified.
    team = cur.get("team") or []
    if team:
        parts.append(f"**Recommended team for {cur_name}:** " + ", ".join(team))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _cmd_render(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_root()
    yml = _yml_path(repo_root, args.lead)
    data = _load_yaml(yml)
    _validate(data, str(yml))
    md_text = _render(data)
    md = _md_path(repo_root, args.lead)
    md.write_text(md_text, encoding="utf-8")
    sys.stderr.write(f"render_kickoff: rendered {yml.name} -> {md.name} (active phase: {data['current_phase']})\n")
    return 0


# ----------------------------------------------------------------------------
# Handoff extraction
# ----------------------------------------------------------------------------
#
# Handoffs follow a markdown convention. The two sections we extract:
#
#   ## Recommended next PM prompt (...)
#
#   ```
#   <prompt body — multi-line>
#   ```
#
#   ## Recommended next agent-team formation (...)
#
#   - **role-1** — description
#   - **role-2** — description
#   ...
#
# The fence under "Recommended next PM prompt" is the canonical extraction.
# Bullets under "Recommended next agent-team formation" are parsed for the
# leading bold token (`**name**`) as the team identifier.


_NEXT_PROMPT_HEADING = re.compile(
    r"^##\s+Recommended\s+next\s+PM\s+prompt\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_TEAM_HEADING = re.compile(
    r"^##\s+Recommended\s+next\s+agent[-\s]team\s+formation\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_FENCE_OPEN = re.compile(r"^```")
_BULLET_BOLD = re.compile(r"^\s*[-*]\s+\*\*([^*]+)\*\*")


def _extract_prompt_from_handoff(handoff_text: str) -> str:
    """Return the first fenced code block under '## Recommended next PM prompt'.

    Raises SystemExit if the section or fence is missing.
    """
    m = _NEXT_PROMPT_HEADING.search(handoff_text)
    if not m:
        raise SystemExit(
            "render_kickoff: handoff is missing a '## Recommended next PM prompt' section"
        )
    rest = handoff_text[m.end():]
    # Find the first fence.
    lines = rest.splitlines()
    in_fence = False
    body_lines: list[str] = []
    for line in lines:
        # Stop if we hit the next H2 before finding a fence — the section is empty.
        if not in_fence and line.startswith("## "):
            raise SystemExit(
                "render_kickoff: 'Recommended next PM prompt' section has no fenced "
                "code block before the next ## heading"
            )
        if _FENCE_OPEN.match(line):
            if not in_fence:
                in_fence = True
                continue
            else:
                # Closing fence — done.
                return "\n".join(body_lines).strip()
        if in_fence:
            body_lines.append(line)
    if in_fence:
        raise SystemExit(
            "render_kickoff: fenced code block under 'Recommended next PM prompt' "
            "is unterminated (no closing ```)"
        )
    raise SystemExit(
        "render_kickoff: 'Recommended next PM prompt' section has no fenced code block"
    )


def _extract_team_from_handoff(handoff_text: str) -> list[str]:
    """Return teammate identifiers from the team-formation bullet list.

    Handoffs commonly list TWO sub-teams under this section: a read-only
    planning team and a follow-up implementation team. We collect bullets
    from both, then dedupe (preserving first-seen order). Section terminates
    at the next ``## `` heading or end-of-document.

    If the section is missing, returns an empty list (team is optional).
    """
    m = _NEXT_TEAM_HEADING.search(handoff_text)
    if not m:
        return []
    rest = handoff_text[m.end():]
    seen: set[str] = set()
    out: list[str] = []
    for line in rest.splitlines():
        if line.startswith("## "):
            break
        bm = _BULLET_BOLD.match(line)
        if bm:
            name = bm.group(1).strip()
            # Skip bullets that describe the team SHAPE rather than a teammate
            # name. Heuristic: real teammate names match the
            # `*-teammate` / `*-lead` / `*-architect` suffix pattern, are
            # contiguous (no spaces), and don't carry trailing colons.
            if ":" in name:
                continue
            if " " in name:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def _read_handoff(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"render_kickoff: handoff not found at {path}")
    return path.read_text(encoding="utf-8")


def _cmd_extract_prompt(args: argparse.Namespace) -> int:
    text = _read_handoff(Path(args.handoff_path).resolve())
    sys.stdout.write(_extract_prompt_from_handoff(text))
    sys.stdout.write("\n")
    return 0


def _cmd_extract_team(args: argparse.Namespace) -> int:
    text = _read_handoff(Path(args.handoff_path).resolve())
    for member in _extract_team_from_handoff(text):
        print(member)
    return 0


# ----------------------------------------------------------------------------
# Phase transition
# ----------------------------------------------------------------------------


def _cmd_transition(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_root()
    yml = _yml_path(repo_root, args.lead)
    handoff = (
        Path(args.from_handoff).resolve()
        if args.from_handoff
        else _handoff_path(repo_root, args.lead)
    )
    handoff_text = _read_handoff(handoff)
    new_first_task = _extract_prompt_from_handoff(handoff_text)
    new_team = _extract_team_from_handoff(handoff_text)

    # Load existing YAML (or refuse — the bootstrap path is `init-from-md`).
    if not yml.exists():
        raise SystemExit(
            f"render_kickoff: {yml} does not exist. "
            f"Run 'init-from-md {args.lead} --current-phase-name <name>' first to "
            f"bootstrap from the legacy hand-authored kickoff."
        )
    data = _load_yaml(yml)
    _validate(data, str(yml))

    # Mark the previously-active phase complete.
    prev_name = data["current_phase"]
    if prev_name in data["phases"]:
        data["phases"][prev_name]["status"] = "complete"

    # Add or update the new phase entry. If it already exists, we update its
    # branch/worktree and overwrite first_task with the freshly-extracted one
    # (the handoff is the authoritative source on what to do next).
    new_phase_name = args.to
    new_branch = args.branch
    new_worktree = args.worktree or data["phases"][prev_name]["worktree"]

    # Mission summary: caller passes one explicitly (recommended), or we fall
    # back to a heuristic (first non-empty line of the extracted prompt).
    new_mission = args.mission
    if not new_mission:
        first_line = next(
            (l.strip() for l in new_first_task.splitlines() if l.strip()),
            "",
        )
        # Strip a leading "You are X..." preamble if present — that's identity,
        # not mission. Prefer a later content-bearing line.
        if first_line.lower().startswith("you are "):
            for l in new_first_task.splitlines():
                ls = l.strip()
                if ls and not ls.lower().startswith("you are "):
                    first_line = ls
                    break
        new_mission = first_line[:80] or f"{new_phase_name} (mission TBD)"

    new_phase_entry = {
        "branch": new_branch,
        "worktree": new_worktree,
        "status": "active",
        "mission": new_mission,
        "first_task": new_first_task,
    }
    if new_team:
        new_phase_entry["team"] = new_team

    data["phases"][new_phase_name] = new_phase_entry
    data["current_phase"] = new_phase_name

    # Validate then save.
    _validate(data, str(yml) + " (post-transition)")
    _save_yaml(yml, data)
    sys.stderr.write(
        f"render_kickoff: transitioned {args.lead} {prev_name} -> {new_phase_name} "
        f"(branch={new_branch}, worktree={new_worktree}, team={new_team or '[]'})\n"
    )

    # Re-render the .md so start_<lead> picks up the new phase next launch.
    md_text = _render(data)
    md = _md_path(repo_root, args.lead)
    md.write_text(md_text, encoding="utf-8")
    sys.stderr.write(f"render_kickoff: re-rendered {md.name}\n")

    # Refresh phase tags inside handoff section headers so a future session
    # reading the handoff doesn't see "(P3 kickoff)" when the next session
    # is on P4. The structured **Next phase:** / **Next branch:** lines are
    # already authoritative for the rotation itself; this just keeps the
    # surrounding prose headers from drifting. Leaves untagged headers
    # (e.g., bare "## Recommended next PM prompt") alone.
    refreshed = _refresh_handoff_phase_headers(handoff, new_phase_name)
    if refreshed:
        sys.stderr.write(
            f"render_kickoff: refreshed phase tags in {handoff.name} "
            f"({refreshed} header(s) updated)\n"
        )

    return 0


# Header lines like:
#   ## Recommended next PM prompt (for next Aria session — P3 kickoff)
#   ## Recommended next agent-team formation (P3 kickoff)
# Both em-dash (U+2014) and ASCII hyphen separators are tolerated in the
# PM-prompt variant. Phase names may contain letters, digits, dots, and
# hyphens (P3, P4, P4-R2, P4.5).
_HANDOFF_PM_HEADER = re.compile(
    r"^(##\s+Recommended next PM prompt\s*\(for next [\w\-]+ session\s+[—\-]\s+)"
    r"[\w.\-]+(\s+kickoff\))",
    re.MULTILINE,
)
_HANDOFF_TEAM_HEADER = re.compile(
    r"^(##\s+Recommended next agent-team formation\s*\()"
    r"[\w.\-]+(\s+kickoff\))",
    re.MULTILINE,
)


def _refresh_handoff_phase_headers(handoff_path: Path, new_phase_name: str) -> int:
    """Rewrite stale phase tags in handoff section headers.

    Returns the number of headers updated. Writes the file back only when
    at least one header changed, so this is idempotent on already-current
    handoffs and a no-op on handoffs that use untagged headers.
    """
    if not handoff_path.exists():
        return 0
    original = handoff_path.read_text(encoding="utf-8")
    updated = original
    count = 0
    for pattern in (_HANDOFF_PM_HEADER, _HANDOFF_TEAM_HEADER):
        updated, n = pattern.subn(rf"\g<1>{new_phase_name}\g<2>", updated)
        count += n
    if updated != original:
        handoff_path.write_text(updated, encoding="utf-8")
    return count


# ----------------------------------------------------------------------------
# Bootstrap from legacy hand-authored markdown
# ----------------------------------------------------------------------------


_BRANCH_LINE = re.compile(r"^\*\*Branch:\*\*\s+`([^`]+)`", re.MULTILINE)
_WORKTREE_LINE = re.compile(r"^\*\*Worktree:\*\*\s+`([^`]+)`", re.MULTILINE)
_WORKSTREAM_LINE = re.compile(r"^\*\*Workstream:\*\*\s+`([^`]+)`", re.MULTILINE)


def _cmd_init_from_md(args: argparse.Namespace) -> int:
    """Bootstrap a YAML config from an existing hand-authored kickoff + handoff.

    The legacy <lead>.md becomes the body of one phase (the one whose name
    is passed via --current-phase-name). The handoff's recommended-prompt
    becomes the next phase's first_task IF --next-phase is also provided.

    This is a one-time migration path; once the YAML exists, transitions go
    through the `transition` subcommand.
    """
    repo_root = _resolve_repo_root()
    yml = _yml_path(repo_root, args.lead)
    if yml.exists() and not args.force:
        raise SystemExit(
            f"render_kickoff: {yml} already exists. Pass --force to overwrite."
        )

    md = _md_path(repo_root, args.lead)
    if not md.exists():
        raise SystemExit(
            f"render_kickoff: legacy kickoff not found at {md}; nothing to migrate."
        )
    md_text = md.read_text(encoding="utf-8")

    # Pull metadata out of the legacy .md.
    branch_m = _BRANCH_LINE.search(md_text)
    worktree_m = _WORKTREE_LINE.search(md_text)
    workstream_m = _WORKSTREAM_LINE.search(md_text)
    if not (branch_m and worktree_m):
        raise SystemExit(
            f"render_kickoff: legacy {md} is missing **Branch:** or **Worktree:** lines"
        )
    branch = branch_m.group(1)
    worktree = worktree_m.group(1)
    workstream = workstream_m.group(1) if workstream_m else f"{args.lead} workstream"

    # Body for the current phase: the entire legacy .md, used as first_task.
    # This is verbatim — no parsing or restructuring. The next render will
    # produce a different shape (since the renderer adds its own header), but
    # the per-phase first_task will read exactly like the legacy kickoff.
    legacy_body = md_text.strip()

    data = {
        "lead": args.lead,
        "workstream": workstream,
        "current_phase": args.current_phase_name,
        "phases": {
            args.current_phase_name: {
                "branch": branch,
                "worktree": worktree,
                "status": "active",
                "mission": f"{args.current_phase_name} (migrated from legacy kickoff)",
                "first_task": legacy_body,
            },
        },
    }
    _validate(data, str(yml) + " (bootstrap)")
    _save_yaml(yml, data)
    sys.stderr.write(
        f"render_kickoff: bootstrapped {yml.name} from legacy {md.name} "
        f"(phase={args.current_phase_name})\n"
    )
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="render_kickoff.py",
        description="Render and transition lead kickoff configs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="Render <lead>.yml -> <lead>.md.")
    p_render.add_argument("lead")
    p_render.set_defaults(func=_cmd_render)

    p_trans = sub.add_parser("transition", help="Promote the current phase to complete and add a new active phase.")
    p_trans.add_argument("lead")
    p_trans.add_argument("--to", required=True, help="New phase name, e.g. P3.")
    p_trans.add_argument("--branch", required=True, help="New phase branch, e.g. agentforge/w2-hitl-p3.")
    p_trans.add_argument("--worktree", default=None, help="New worktree path. Defaults to current phase's worktree.")
    p_trans.add_argument("--mission", default=None, help="Short mission summary (one line). Heuristic if omitted.")
    p_trans.add_argument(
        "--from-handoff",
        default=None,
        help="Path to the handoff markdown. Defaults to "
             ".gauntlet/week2/handoffs/<lead>-handoff.md.",
    )
    p_trans.set_defaults(func=_cmd_transition)

    p_ep = sub.add_parser("extract-prompt", help="Print the next PM prompt code block from a handoff.")
    p_ep.add_argument("handoff_path")
    p_ep.set_defaults(func=_cmd_extract_prompt)

    p_et = sub.add_parser("extract-team", help="Print the next agent-team bullet list from a handoff.")
    p_et.add_argument("handoff_path")
    p_et.set_defaults(func=_cmd_extract_team)

    p_init = sub.add_parser("init-from-md", help="Bootstrap a YAML config from a legacy hand-authored kickoff.")
    p_init.add_argument("lead")
    p_init.add_argument("--current-phase-name", required=True, help="Name to label the current phase under (e.g. P2).")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing YAML.")
    p_init.set_defaults(func=_cmd_init_from_md)

    args = parser.parse_args()
    try:
        return args.func(args)
    except SystemExit as e:
        # SystemExit with str arg: print to stderr, exit non-zero.
        if isinstance(e.code, str):
            sys.stderr.write(e.code + "\n")
            return 1
        return e.code or 0


if __name__ == "__main__":
    sys.exit(main())
