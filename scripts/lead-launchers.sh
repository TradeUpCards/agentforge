#!/usr/bin/env bash
#
# Lead launchers for AgentForge parallel work.
#
# Usage — source from your ~/.bashrc (or ~/.zshrc):
#   source /c/Dev/GauntletAI/AgentForge/scripts/lead-launchers.sh   # Git Bash on Windows
#   source ~/Dev/GauntletAI/AgentForge/scripts/lead-launchers.sh    # WSL / macOS / Linux
#
# Path overrides (set before sourcing if needed):
#   export AGENTFORGE_ROOT="$HOME/Dev/GauntletAI/AgentForge"
#   export CURSOR_WORKSPACE_FILE="$HOME/AgentForge.code-workspace"
#
# Dependency: jq (for JSON workspace manipulation).
#   Git Bash: ships with jq in recent Git for Windows installers
#   WSL/Ubuntu: sudo apt install jq
#   macOS: brew install jq
#
# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------
# The kickoff prompt at .gauntlet/week2/kickoff/<name>.md drives BOTH the
# lead's identity AND the launcher's branch/worktree binding. The launcher
# greps these two lines out of the kickoff:
#
#   **Branch:** `agentforge/w2-hitl-extraction`
#   **Worktree:** `../AgentForge-hitl`
#
# To reassign a lead to a new workstream:
#   1. Edit .gauntlet/week2/kickoff/<name>.md — update the Branch and
#      Worktree lines (and the rest of the kickoff content)
#   2. Edit .gauntlet/week2/in-flight.md — update the Leads table row
#   3. (optional) git worktree remove <old-worktree> if you're done with it
#   4. Next call to start_<name> creates the new worktree if missing
#
# ---------------------------------------------------------------------------
# Cursor workspace integration
# ---------------------------------------------------------------------------
# start_<name> adds the lead's worktree to a multi-root Cursor workspace.
# stop_<name> removes it. Default workspace file lives at
# ${AGENTFORGE_PARENT}/AgentForge.code-workspace; override with the
# environment variable CURSOR_WORKSPACE_FILE.
#
# Cursor watches the workspace file and updates the file explorer live.
# Sometimes a Ctrl+Shift+P → "Reload Window" is needed.
#
# start_<name> also sets the integrated terminal tab title to the lead's
# name (Aria, Bram, …). stop_<name> resets the title to "AgentForge".
#
# Functions:
#   start_lead <name>   # generic launcher
#   start_aria / start_bram / start_cleo
#   stop_lead <name>    # generic stopper (workspace untrack + title reset)
#   stop_aria  / stop_bram  / stop_cleo

AGENTFORGE_ROOT="${AGENTFORGE_ROOT:-/c/Dev/GauntletAI/AgentForge}"
AGENTFORGE_PARENT="$(dirname "$AGENTFORGE_ROOT")"

# ---------------------------------------------------------------------------
# Kickoff parsing
# ---------------------------------------------------------------------------

_kickoff_field() {
    local field="$1"
    local prompt_path="$2"
    sed -n "s/^\*\*${field}:\*\* \`\([^\`]*\)\`.*/\1/p" "$prompt_path" | head -n 1
}

_resolve_worktree_path() {
    local raw="$1"
    case "$raw" in
        /*|[A-Za-z]:[\\/]*) printf '%s\n' "$raw" ;;
        ../*) printf '%s\n' "$AGENTFORGE_PARENT/${raw#../}" ;;
        *)    printf '%s\n' "$AGENTFORGE_PARENT/$raw" ;;
    esac
}

# ---------------------------------------------------------------------------
# Cross-worktree directory junctions
# ---------------------------------------------------------------------------
# Two repo-root directories are gitignored AND need to be visible from every
# worktree:
#
#   .gauntlet/  cohort-property PRDs, personal stories, handoffs, kickoff
#               prompts, audits. Cross-lead coordination state lives here.
#   .claude/    agents/<name>.md (drives the agent badge in Cursor's
#               terminal), skills/<name>/SKILL.md (drives /aria, /bram,
#               /cleo and other slash commands), settings.json, hooks.
#
# Because git worktrees only inherit *tracked* content, a fresh worktree
# has neither directory. Solution: a directory junction (Windows) /
# symlink (Unix) from <worktree>/<dir> → <main checkout>/<dir>. Single
# source of truth on disk; edits in any worktree are immediately visible
# everywhere; nothing leaks to public mirrors; no manual sync.
#
# WARNING: before `git worktree remove <worktree>`, remove BOTH junctions
# first to avoid any risk of recursing into the canonical content:
#   rm <worktree>/.gauntlet <worktree>/.claude              # Unix
#   cmd //c rmdir <worktree>\.gauntlet                      # Windows
#   cmd //c rmdir <worktree>\.claude                        # (non-
#       recursive removes the junction, not the target)

# _ensure_junction <target> <source> <sentinel-relpath>
#   Creates a directory junction (Windows) / symlink (Unix) at $target
#   pointing to $source if not already in place. Detects existing
#   junction by checking for $target/$sentinel-relpath. Idempotent.
_ensure_junction() {
    local target="$1"
    local source="$2"
    local sentinel="$3"

    # Sentinel-file check: if a known file is readable through $target,
    # the junction (or symlink) is already in place. Works for both
    # Windows junctions and Unix symlinks.
    if [ -f "$target/$sentinel" ]; then
        return 0
    fi

    # Empty dir? Remove so junction can take its place.
    if [ -d "$target" ] && [ -z "$(ls -A "$target" 2>/dev/null)" ]; then
        rmdir "$target" 2>/dev/null
    fi

    if [ -e "$target" ]; then
        echo "Cannot create junction at $target — path exists with content." >&2
        echo "  Resolve manually: ensure $target is empty or a junction to $source." >&2
        return 1
    fi

    # Windows (Git Bash / MSYS): use cmd.exe + mklink /J via a temp .bat file.
    # The direct `cmd //c "mklink /J ..."` invocation has been observed to drop
    # its argument under Git Bash inside Cursor's integrated terminal, leaving
    # cmd.exe interactive and hanging the launcher (Ctrl+C does not break out).
    # Routing through a temp .bat eliminates all bash↔cmd quoting and path-
    # conversion ambiguity. mklink /J does not require admin.
    if command -v cmd.exe >/dev/null 2>&1 || [ -n "$WINDIR" ]; then
        local target_win source_win
        if command -v cygpath >/dev/null 2>&1; then
            target_win="$(cygpath -w "$target")"
            source_win="$(cygpath -w "$source")"
        else
            target_win="$(printf '%s' "$target" | sed 's|/|\\|g')"
            source_win="$(printf '%s' "$source" | sed 's|/|\\|g')"
        fi

        local batfile batfile_win
        batfile="$(mktemp).bat"
        # cmd.exe expects CRLF line endings in .bat files.
        printf 'mklink /J "%s" "%s"\r\n' "$target_win" "$source_win" > "$batfile"
        if command -v cygpath >/dev/null 2>&1; then
            batfile_win="$(cygpath -w "$batfile")"
        else
            batfile_win="$batfile"
        fi

        if cmd.exe /c "$batfile_win" >/dev/null 2>&1; then
            rm -f "$batfile"
            echo "Junctioned $target -> $source"
            return 0
        fi
        rm -f "$batfile"
        echo "Failed to junction $target -> $source via mklink /J" >&2
        return 1
    fi

    # Unix (macOS / Linux / WSL): symlink.
    if ln -s "$source" "$target"; then
        echo "Symlinked $target -> $source"
        return 0
    fi
    echo "Failed to symlink $target -> $source" >&2
    return 1
}

_ensure_gauntlet_junction() {
    local worktree="$1"
    _ensure_junction \
        "$worktree/.gauntlet" \
        "$AGENTFORGE_ROOT/.gauntlet" \
        "week2/in-flight.md"
}

_ensure_claude_junction() {
    local worktree="$1"
    _ensure_junction \
        "$worktree/.claude" \
        "$AGENTFORGE_ROOT/.claude" \
        "agents/gauntlet-team-lead.md"
}

# ---------------------------------------------------------------------------
# Cursor workspace JSON management (requires jq)
# ---------------------------------------------------------------------------

_workspace_file() {
    printf '%s\n' "${CURSOR_WORKSPACE_FILE:-$AGENTFORGE_PARENT/AgentForge.code-workspace}"
}

_require_jq() {
    if ! command -v jq >/dev/null 2>&1; then
        echo "jq not found. Install:" >&2
        echo "  Git Bash: bundled with recent Git for Windows; reinstall to get it" >&2
        echo "  WSL/Ubuntu: sudo apt install jq" >&2
        echo "  macOS: brew install jq" >&2
        return 1
    fi
}

_ensure_workspace_file() {
    local ws
    ws="$(_workspace_file)"
    if [ -f "$ws" ]; then return 0; fi
    cat > "$ws" <<EOF
{
  "folders": [
    { "path": "$AGENTFORGE_ROOT" }
  ],
  "settings": {}
}
EOF
    echo "Created Cursor workspace at $ws"
}

_add_workspace_folder() {
    local folder="$1"
    _require_jq || return $?
    _ensure_workspace_file
    local ws
    ws="$(_workspace_file)"
    local tmp
    tmp="$(mktemp)"
    if jq --arg p "$folder" '
        if (.folders // [] | map(.path) | index($p)) == null then
            .folders = (.folders // []) + [{path: $p}]
        else . end
    ' "$ws" > "$tmp"; then
        if ! cmp -s "$ws" "$tmp"; then
            mv "$tmp" "$ws"
            echo "Added $folder to Cursor workspace"
        else
            rm -f "$tmp"
        fi
    else
        rm -f "$tmp"
        echo "Failed to update workspace file" >&2
        return 1
    fi
}

_remove_workspace_folder() {
    local folder="$1"
    _require_jq || return $?
    local ws
    ws="$(_workspace_file)"
    [ -f "$ws" ] || return 0
    local tmp
    tmp="$(mktemp)"
    if jq --arg p "$folder" '.folders |= map(select(.path != $p))' "$ws" > "$tmp"; then
        if ! cmp -s "$ws" "$tmp"; then
            mv "$tmp" "$ws"
            echo "Removed $folder from Cursor workspace"
        else
            rm -f "$tmp"
        fi
    else
        rm -f "$tmp"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Terminal tab title (works in Cursor / VS Code / xterm-compatible terminals)
# ---------------------------------------------------------------------------

_set_terminal_title() {
    # OSC 0 — sets icon + window title; Cursor uses this for terminal tabs.
    printf '\033]0;%s\007' "$1"
}

# ---------------------------------------------------------------------------
# Public API: start_lead / stop_lead
# ---------------------------------------------------------------------------

_start_lead() {
    local name="$1"
    if [ -z "$name" ]; then
        cat >&2 <<EOF
Usage: start_lead <name> [-c|--continue | -r|--resume [search]] [--fork]

  <name>           Lead identifier (aria, bram, cleo, ...).
  -c, --continue   Resume most recent conversation in this worktree.
  -r, --resume     Open Claude's resume picker (optionally with a search term
                   to filter previous sessions).
  --fork           When resuming, fork to a new session ID so the original
                   stays intact. Only meaningful with --continue / --resume.

Default (no flags): fresh session seeded with the lead's kickoff prompt.
EOF
        return 2
    fi
    shift

    # Parse optional resume flags. Defaults: fresh launch with kickoff prompt.
    local resume_mode=""        # "" | "continue" | "resume"
    local resume_search=""
    local fork_flag=""
    while [ $# -gt 0 ]; do
        case "$1" in
            -c|--continue)
                resume_mode="continue"
                shift
                ;;
            -r|--resume)
                resume_mode="resume"
                shift
                # Optional search term — consume only if next arg isn't a flag.
                if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
                    resume_search="$1"
                    shift
                fi
                ;;
            --fork|--fork-session)
                fork_flag="--fork-session"
                shift
                ;;
            -h|--help)
                _start_lead   # re-trigger usage by calling with no args
                return 0
                ;;
            *)
                echo "start_lead: unknown option: $1" >&2
                return 2
                ;;
        esac
    done

    local prompt_path="$AGENTFORGE_ROOT/.gauntlet/week2/kickoff/$name.md"
    if [ ! -f "$prompt_path" ]; then
        echo "Kickoff prompt not found at $prompt_path" >&2
        echo "Create it before launching this lead." >&2
        return 1
    fi

    local branch worktree_raw worktree
    branch="$(_kickoff_field "Branch" "$prompt_path")"
    worktree_raw="$(_kickoff_field "Worktree" "$prompt_path")"
    if [ -z "$branch" ] || [ -z "$worktree_raw" ]; then
        echo "Kickoff $prompt_path is missing **Branch:** or **Worktree:** metadata line" >&2
        return 1
    fi
    worktree="$(_resolve_worktree_path "$worktree_raw")"

    if [ ! -d "$worktree" ]; then
        echo "Creating worktree at $worktree on branch $branch..."
        if git -C "$AGENTFORGE_ROOT" rev-parse --verify "$branch" >/dev/null 2>&1; then
            git -C "$AGENTFORGE_ROOT" worktree add "$worktree" "$branch" || return $?
        else
            git -C "$AGENTFORGE_ROOT" worktree add "$worktree" -b "$branch" || return $?
        fi
    fi

    # Ensure .gauntlet/ junction so handoffs / in-flight / kickoff prompts
    # are visible from inside the worktree. Idempotent — runs every time
    # in case the worktree was created manually without the launcher.
    _ensure_gauntlet_junction "$worktree" || return $?

    # Ensure .claude/ junction so the per-lead agent file
    # (.claude/agents/<name>.md) and slash-command skills
    # (.claude/skills/<name>/SKILL.md) are findable from inside the
    # worktree. Without this, --agent <name> below would fail or fall
    # back to the wrong identity.
    _ensure_claude_junction "$worktree" || return $?

    # Add to Cursor workspace (idempotent).
    _add_workspace_folder "$worktree"

    # Title the terminal tab — capitalize the lead name.
    local title_case
    title_case="$(printf '%s' "$name" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
    _set_terminal_title "$title_case"

    local prompt
    prompt="$(cat "$prompt_path")"

    # Tell VS Code / Cursor the cwd so ${cwdFolder} in tabs.title reflects the
    # worktree (e.g. "AgentForge-hitl") instead of wherever the user opened the
    # terminal. Shell integration normally emits OSC 633;P;Cwd= on every prompt,
    # but we're about to launch claude directly without a prompt fire — so emit
    # it manually here. Use cygpath -w when available so the path matches the
    # format Cursor expects on Windows.
    local worktree_for_cwd="$worktree"
    if command -v cygpath >/dev/null 2>&1; then
        worktree_for_cwd="$(cygpath -w "$worktree")"
    fi
    printf '\033]633;P;Cwd=%s\007' "$worktree_for_cwd"

    # The --agent flag value is what Cursor's terminal renders as the
    # session badge in the top-right (e.g. "bram" instead of
    # "gauntlet-team-lead"). Each lead has a thin agent file at
    # .claude/agents/<name>.md that inherits gauntlet-team-lead's hard
    # rules and overrides only the identity line. If the lead-specific
    # file is missing, fall back to gauntlet-team-lead so the launcher
    # still works for ad-hoc lead names.
    local agent_id="$name"
    if [ ! -f "$AGENTFORGE_ROOT/.claude/agents/$name.md" ]; then
        echo "No agent file at .claude/agents/$name.md — falling back to gauntlet-team-lead." >&2
        agent_id="gauntlet-team-lead"
    fi

    # Build claude args. --teammate-mode in-process is constant; --agent picks
    # the lead identity; resume flags (if any) replace the kickoff-prompt
    # positional argument because resuming an existing conversation should
    # not re-inject the kickoff system message.
    local -a claude_args=(--agent "$agent_id" --teammate-mode in-process)
    [ -n "$fork_flag" ] && claude_args+=("$fork_flag")

    case "$resume_mode" in
        continue)
            claude_args+=(--continue)
            echo "Launching Claude as $title_case in $worktree (branch: $branch, agent: $agent_id, mode: continue${fork_flag:+ +fork})..."
            (cd "$worktree" && claude "${claude_args[@]}")
            ;;
        resume)
            if [ -n "$resume_search" ]; then
                claude_args+=(--resume "$resume_search")
            else
                claude_args+=(--resume)
            fi
            echo "Launching Claude as $title_case in $worktree (branch: $branch, agent: $agent_id, mode: resume picker${resume_search:+ filter='$resume_search'}${fork_flag:+ +fork})..."
            (cd "$worktree" && claude "${claude_args[@]}")
            ;;
        *)
            echo "Launching Claude as $title_case in $worktree (branch: $branch, agent: $agent_id, mode: fresh)..."
            (cd "$worktree" && claude "${claude_args[@]}" "$prompt")
            ;;
    esac
}

_stop_lead() {
    local name="$1"
    if [ -z "$name" ]; then
        echo "Usage: stop_lead <name>" >&2
        return 2
    fi

    local prompt_path="$AGENTFORGE_ROOT/.gauntlet/week2/kickoff/$name.md"
    if [ ! -f "$prompt_path" ]; then
        echo "Kickoff prompt not found at $prompt_path — cannot determine which worktree to untrack" >&2
        return 1
    fi
    local worktree_raw worktree
    worktree_raw="$(_kickoff_field "Worktree" "$prompt_path")"
    if [ -z "$worktree_raw" ]; then
        echo "Kickoff $prompt_path is missing **Worktree:** metadata line" >&2
        return 1
    fi
    worktree="$(_resolve_worktree_path "$worktree_raw")"

    _remove_workspace_folder "$worktree"
    _set_terminal_title "AgentForge"

    local title_case
    title_case="$(printf '%s' "$name" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
    echo "Stopped tracking $title_case. Worktree at $worktree is untouched — run start_$name to resume."
}

_relative_age() {
    # Convert a unix timestamp into a human-friendly relative age string.
    local mtime="$1"
    [ -z "$mtime" ] && return
    local now diff
    now="$(date +%s)"
    diff=$(( now - mtime ))
    if [ $diff -lt 60 ]; then
        echo "${diff}s ago"
    elif [ $diff -lt 3600 ]; then
        echo "$(( diff / 60 ))m ago"
    elif [ $diff -lt 86400 ]; then
        echo "$(( diff / 3600 ))h ago"
    else
        echo "$(( diff / 86400 ))d ago"
    fi
}

_inflight_block() {
    # Extract the "### <Lead> ..." subsection from in-flight.md, up to the
    # next "### " heading or section break. Returns nothing if the lead
    # has no In Flight entry.
    local lead_title="$1"
    local file="$AGENTFORGE_ROOT/.gauntlet/week2/in-flight.md"
    [ -f "$file" ] || return 1
    awk -v lead="$lead_title" '
        /^### / {
            if ($2 == lead) { in_section = 1; next }
            else if (in_section) { exit }
        }
        /^## / && in_section { exit }
        /^---$/ && in_section { exit }
        in_section { print }
    ' "$file"
}

_inflight_field() {
    # Pull a single bullet field (e.g. "Status", "Files locked", "Next
    # checkpoint") out of the lead's in-flight block.
    local lead_title="$1"
    local field="$2"
    _inflight_block "$lead_title" \
        | sed -n "s/^- \*\*${field}:\*\* *\(.*\)/\1/p" \
        | head -1
}

_list_leads() {
    local kickoff_dir="$AGENTFORGE_ROOT/.gauntlet/week2/kickoff"
    if [ ! -d "$kickoff_dir" ]; then
        echo "No kickoff directory at $kickoff_dir" >&2
        return 1
    fi

    local first=1
    for f in "$kickoff_dir"/*.md; do
        [ -f "$f" ] || continue
        [ $first -eq 0 ] && echo
        first=0

        local name title_case workstream branch worktree_raw worktree
        name="$(basename "$f" .md)"
        title_case="$(printf '%s' "$name" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
        workstream="$(_kickoff_field "Workstream" "$f")"
        branch="$(_kickoff_field "Branch" "$f")"
        worktree_raw="$(_kickoff_field "Worktree" "$f")"
        worktree="$(_resolve_worktree_path "$worktree_raw")"

        if [ -n "$workstream" ]; then
            printf '%s — %s\n' "$title_case" "$workstream"
        else
            printf '%s\n' "$title_case"
        fi

        printf '  branch:    %s\n' "${branch:-<missing>}"

        local worktree_status
        if [ -d "$worktree" ]; then
            worktree_status="active"
        else
            worktree_status="not yet created"
        fi
        printf '  worktree:  %s  (%s)\n' "${worktree_raw:-<missing>}" "$worktree_status"

        # Last commit on the lead's branch — proxy for "currently working on what".
        if [ -n "$branch" ] && git -C "$AGENTFORGE_ROOT" rev-parse --verify "$branch" >/dev/null 2>&1; then
            local last_commit
            last_commit="$(git -C "$AGENTFORGE_ROOT" log -1 --format='%s — %cr' "$branch" 2>/dev/null)"
            [ -n "$last_commit" ] && printf '  last:      %s\n' "$last_commit"
        fi

        # Handoff age — proxy for "when did this lead last save state".
        local handoff="$AGENTFORGE_ROOT/.gauntlet/week2/handoffs/$name-handoff.md"
        if [ -f "$handoff" ]; then
            local mtime age
            mtime="$(stat -c '%Y' "$handoff" 2>/dev/null)"
            age="$(_relative_age "$mtime")"
            printf '  handoff:   updated %s\n' "${age:-(see file)}"
        else
            printf '  handoff:   not yet written\n'
        fi

        # In-flight state — what the lead said they're doing right now.
        local status files_locked next_checkpoint
        status="$(_inflight_field "$title_case" "Status")"
        files_locked="$(_inflight_field "$title_case" "Files locked")"
        next_checkpoint="$(_inflight_field "$title_case" "Next checkpoint")"
        if [ -n "$status" ] || [ -n "$files_locked" ] || [ -n "$next_checkpoint" ]; then
            [ -n "$status" ]          && printf '  status:    %s\n' "$status"
            [ -n "$files_locked" ]    && printf '  locked:    %s\n' "$files_locked"
            [ -n "$next_checkpoint" ] && printf '  next:      %s\n' "$next_checkpoint"
        else
            printf '  in-flight: no entry in in-flight.md (lead has not updated)\n'
        fi
    done
}

list_leads() { _list_leads "$@"; }
list-leads() { _list_leads "$@"; }

start_lead() { _start_lead "$@"; }
stop_lead()  { _stop_lead "$@"; }

# snake_case (bash convention) — "$@" forwards resume flags (-c, -r, --fork)
start_aria() { _start_lead "aria" "$@"; }
start_bram() { _start_lead "bram" "$@"; }
start_cleo() { _start_lead "cleo" "$@"; }

stop_aria() { _stop_lead "aria"; }
stop_bram() { _stop_lead "bram"; }
stop_cleo() { _stop_lead "cleo"; }

# hyphenated aliases (matches PowerShell muscle memory)
start-aria() { _start_lead "aria" "$@"; }
start-bram() { _start_lead "bram" "$@"; }
start-cleo() { _start_lead "cleo" "$@"; }

stop-aria() { _stop_lead "aria"; }
stop-bram() { _stop_lead "bram"; }
stop-cleo() { _stop_lead "cleo"; }

start-lead() { _start_lead "$@"; }
stop-lead()  { _stop_lead "$@"; }
