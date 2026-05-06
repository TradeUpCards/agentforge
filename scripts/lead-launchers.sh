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
# .gauntlet junction (cross-worktree coordination)
# ---------------------------------------------------------------------------
# .gauntlet/ is gitignored (cohort-property PRDs, personal stories, handoffs,
# kickoff prompts, audits). Because git worktrees only inherit *tracked*
# content, a fresh worktree has no .gauntlet/ — but cross-lead coordination
# files (handoffs, in-flight, kickoff) live there and must be visible from
# every worktree. Solution: a directory junction (Windows) / symlink (Unix)
# from <worktree>/.gauntlet → <main checkout>/.gauntlet. Single source of
# truth, zero exposure to public mirrors, no manual sync.
#
# Idempotent: detects existing junction via a sentinel file and is safe to
# re-run on every start_lead invocation.
#
# WARNING: before `git worktree remove <worktree>`, remove the junction
# first to avoid any risk of recursing into the canonical .gauntlet:
#   rm <worktree>/.gauntlet     # Unix
#   cmd //c rmdir <worktree>\.gauntlet   # Windows (non-recursive removes
#                                         # the junction, not the target)

_ensure_gauntlet_junction() {
    local worktree="$1"
    local target="$worktree/.gauntlet"
    local source="$AGENTFORGE_ROOT/.gauntlet"

    # Sentinel-file check: if a known coordination file is readable through
    # $target, the junction (or symlink) is already in place. Works for
    # both Windows junctions and Unix symlinks.
    if [ -f "$target/week2/in-flight.md" ]; then
        return 0
    fi

    # Empty .gauntlet dir? Remove so junction can take its place.
    if [ -d "$target" ] && [ -z "$(ls -A "$target" 2>/dev/null)" ]; then
        rmdir "$target" 2>/dev/null
    fi

    if [ -e "$target" ]; then
        echo "Cannot create .gauntlet junction at $target — path exists with content." >&2
        echo "  Resolve manually: ensure $target is empty or a junction to $source." >&2
        return 1
    fi

    # Windows (Git Bash / MSYS): use cmd.exe + mklink /J (no admin needed).
    if command -v cmd.exe >/dev/null 2>&1 || [ -n "$WINDIR" ]; then
        local target_win source_win
        if command -v cygpath >/dev/null 2>&1; then
            target_win="$(cygpath -w "$target")"
            source_win="$(cygpath -w "$source")"
        else
            target_win="$(printf '%s' "$target" | sed 's|/|\\|g')"
            source_win="$(printf '%s' "$source" | sed 's|/|\\|g')"
        fi
        if MSYS_NO_PATHCONV=1 cmd //c "mklink /J \"$target_win\" \"$source_win\"" >/dev/null; then
            echo "Junctioned $target -> $source"
            return 0
        fi
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
        echo "Usage: start_lead <name>" >&2
        return 2
    fi

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

    # Add to Cursor workspace (idempotent).
    _add_workspace_folder "$worktree"

    # Title the terminal tab — capitalize the lead name.
    local title_case
    title_case="$(printf '%s' "$name" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
    _set_terminal_title "$title_case"

    local prompt
    prompt="$(cat "$prompt_path")"

    echo "Launching Claude as $title_case in $worktree (branch: $branch)..."
    (cd "$worktree" && claude --agent gauntlet-team-lead --teammate-mode in-process "$prompt")
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

start_lead() { _start_lead "$@"; }
stop_lead()  { _stop_lead "$@"; }

# snake_case (bash convention)
start_aria() { _start_lead "aria"; }
start_bram() { _start_lead "bram"; }
start_cleo() { _start_lead "cleo"; }

stop_aria() { _stop_lead "aria"; }
stop_bram() { _stop_lead "bram"; }
stop_cleo() { _stop_lead "cleo"; }

# hyphenated aliases (matches PowerShell muscle memory)
start-aria() { _start_lead "aria"; }
start-bram() { _start_lead "bram"; }
start-cleo() { _start_lead "cleo"; }

stop-aria() { _stop_lead "aria"; }
stop-bram() { _stop_lead "bram"; }
stop-cleo() { _stop_lead "cleo"; }

start-lead() { _start_lead "$@"; }
stop-lead()  { _stop_lead "$@"; }
