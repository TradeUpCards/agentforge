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
#   3. (optional) finish_<name> if you're done with the old worktree
#   4. Next call to start_<name> creates the new worktree if missing
#
# ---------------------------------------------------------------------------
# Cursor workspace integration
# ---------------------------------------------------------------------------
# start_<name> adds the lead's worktree to a multi-root Cursor workspace.
# stop_<name> removes it. finish_<name> tears down the worktree, branch,
# junctions, and workspace entry once the branch has merged. Default
# workspace file lives at ${AGENTFORGE_PARENT}/AgentForge.code-workspace;
# override with the environment variable CURSOR_WORKSPACE_FILE.
#
# Cursor watches the workspace file and updates the file explorer live.
# Sometimes a Ctrl+Shift+P → "Reload Window" is needed.
#
# start_<name> also sets the integrated terminal tab title to the lead's
# name (Aria, Bram, …). stop_<name> / finish_<name> reset it to "AgentForge".
#
# Functions:
#   start_lead <name>    # generic launcher
#   start_aria / start_bram / start_cleo
#   stop_lead <name>     # generic stopper (workspace untrack + title reset)
#   stop_aria  / stop_bram  / stop_cleo
#   finish_lead <name>   # safe teardown (worktree + branch + junctions)
#   finish_aria / finish_bram / finish_cleo

AGENTFORGE_ROOT="${AGENTFORGE_ROOT:-/c/Dev/GauntletAI/AgentForge}"
AGENTFORGE_PARENT="$(dirname "$AGENTFORGE_ROOT")"

# ---------------------------------------------------------------------------
# Kickoff parsing
# ---------------------------------------------------------------------------

# _kickoff_field <field> <prompt-path>
#   Lenient sed-based extractor. Used by _list_leads for arbitrary fields
#   (e.g. "Workstream") where strict validation is overkill. Do NOT use
#   for branch/worktree resolution that drives destructive operations —
#   use _parse_kickoff for that.
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

# _parse_kickoff <name>
#   Strict resolver for kickoff Branch + Worktree fields. Validates:
#     - kickoff file exists at $AGENTFORGE_ROOT/.gauntlet/week2/kickoff/<name>.md
#     - exactly one **Branch:** match (backtick-wrapped value)
#     - exactly one **Worktree:** match (backtick-wrapped value)
#     - branch matches ^[A-Za-z0-9/_.-]+$ (no whitespace, no shell metacharacters)
#     - worktree matches ^\.\./[A-Za-z0-9._-]+$ (single sibling segment, no
#       embedded `..` or nested components)
#     - resolves worktree to an absolute path via realpath -m if available
#       and a shell-native fallback otherwise
#   On success, exports two globals for the caller to copy locally:
#     _PARSED_BRANCH    validated branch name
#     _PARSED_WORKTREE  resolved absolute worktree path
#   On any failure, prints a one-line message to stderr and returns non-zero.
_parse_kickoff() {
    local name="$1"
    if [ -z "$name" ]; then
        echo "_parse_kickoff: missing <name> argument" >&2
        return 2
    fi

    local prompt_path="$AGENTFORGE_ROOT/.gauntlet/week2/kickoff/$name.md"
    if [ ! -f "$prompt_path" ]; then
        echo "_parse_kickoff: kickoff file not found at $prompt_path" >&2
        return 1
    fi

    # Anchored regex extraction. Backticks wrap the value, e.g.:
    #   **Branch:** `agentforge/w2-hitl-extraction`
    # The grep captures the backticked literal exactly (no trailing chars).
    local branch_lines worktree_lines
    branch_lines="$(grep -E "^\*\*Branch:\*\*[[:space:]]+\`[^\`]+\`" "$prompt_path" || true)"
    worktree_lines="$(grep -E "^\*\*Worktree:\*\*[[:space:]]+\`[^\`]+\`" "$prompt_path" || true)"

    local branch_count worktree_count
    branch_count=$(printf '%s\n' "$branch_lines" | grep -c . || true)
    worktree_count=$(printf '%s\n' "$worktree_lines" | grep -c . || true)
    # printf '\n' yields a single empty line that grep -c counts as 0 — guard.
    [ -z "$branch_lines" ] && branch_count=0
    [ -z "$worktree_lines" ] && worktree_count=0

    if [ "$branch_count" -eq 0 ]; then
        echo "_parse_kickoff: no **Branch:** \`...\` line found in $prompt_path" >&2
        return 1
    fi
    if [ "$branch_count" -gt 1 ]; then
        echo "_parse_kickoff: multiple **Branch:** lines in $prompt_path (expected exactly one)" >&2
        return 1
    fi
    if [ "$worktree_count" -eq 0 ]; then
        echo "_parse_kickoff: no **Worktree:** \`...\` line found in $prompt_path" >&2
        return 1
    fi
    if [ "$worktree_count" -gt 1 ]; then
        echo "_parse_kickoff: multiple **Worktree:** lines in $prompt_path (expected exactly one)" >&2
        return 1
    fi

    local branch worktree_raw
    branch=$(printf '%s' "$branch_lines" | sed -n "s/^\*\*Branch:\*\*[[:space:]]\+\`\([^\`]*\)\`.*/\1/p" | head -n 1)
    worktree_raw=$(printf '%s' "$worktree_lines" | sed -n "s/^\*\*Worktree:\*\*[[:space:]]\+\`\([^\`]*\)\`.*/\1/p" | head -n 1)

    if [ -z "$branch" ]; then
        echo "_parse_kickoff: failed to extract branch value from $prompt_path" >&2
        return 1
    fi
    if [ -z "$worktree_raw" ]; then
        echo "_parse_kickoff: failed to extract worktree value from $prompt_path" >&2
        return 1
    fi

    # Validate branch shape — no whitespace, no shell metacharacters.
    if ! printf '%s' "$branch" | grep -Eq '^[A-Za-z0-9/_.-]+$'; then
        echo "_parse_kickoff: branch '$branch' contains invalid characters (allowed: A-Za-z0-9/_.-)" >&2
        return 1
    fi

    # Validate worktree shape — must be ../<single-segment>, no embedded `..`,
    # no nested directory components, no shell metacharacters. This rejects
    # absolute paths and sideways escapes like ../foo/../bar.
    if ! printf '%s' "$worktree_raw" | grep -Eq '^\.\./[A-Za-z0-9._-]+$'; then
        echo "_parse_kickoff: worktree '$worktree_raw' must match ^\\.\\./[A-Za-z0-9._-]+\$ (single sibling segment)" >&2
        return 1
    fi

    # Resolve to absolute path.
    local rel="$AGENTFORGE_PARENT/${worktree_raw#../}"
    local abs
    if command -v realpath >/dev/null 2>&1; then
        abs="$(realpath -m "$rel" 2>/dev/null)"
    else
        # Shell-native fallback for environments without GNU coreutils.
        # Step 5 above forbids embedded `..`, so pwd -W's lack of `..`
        # normalization doesn't matter here.
        abs="$(cd "$(dirname "$rel")" 2>/dev/null && printf '%s/%s' "$(pwd -W 2>/dev/null || pwd)" "$(basename "$rel")")"
    fi
    if [ -z "$abs" ]; then
        echo "_parse_kickoff: failed to resolve absolute path for $rel" >&2
        return 1
    fi

    _PARSED_BRANCH="$branch"
    _PARSED_WORKTREE="$abs"
    return 0
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
# Teardown is handled by finish_<lead>, which calls _remove_junction with
# canonical-target verification before removal — never recurses into the
# canonical content even if the link was manually re-pointed.

# PowerShell single-quoted literals: only ' needs escaping (by doubling).
# Windows path components cannot contain NUL or newline (filesystem
# invariant), so doubling-the-quote is provably sufficient. Do NOT
# "harden" this with backslash escaping later — that would break
# PowerShell parsing.
_ps_quote() { printf "'%s'" "${1//\'/\'\'}"; }

# _is_junction <path>
#   Returns 0 if <path> is a Windows junction or symlink (per PowerShell
#   Get-Item ... LinkType), 1 otherwise. Uses a baked-in-path tempfile
#   .ps1 to avoid bash↔PowerShell argument-passing surprises through MSYS.
_is_junction() {
    local target="$1"
    [ -z "$target" ] && return 1

    # Cheap pre-check: if the path doesn't exist at all, skip the PS shell-out.
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        return 1
    fi

    local target_w
    if command -v cygpath >/dev/null 2>&1; then
        target_w="$(cygpath -w "$target" 2>/dev/null)" || return 1
    else
        target_w="$target"
    fi

    if ! command -v powershell.exe >/dev/null 2>&1; then
        # Unix host — fall back to POSIX symlink test.
        [ -L "$target" ] && return 0
        return 1
    fi

    local tmpfile rc out
    tmpfile=$(mktemp --suffix=.ps1) || return 1
    {
        echo "\$ErrorActionPreference = 'Stop'"
        echo "\$target = $(_ps_quote "$target_w")"
        echo "try {"
        echo "    \$item = Get-Item -LiteralPath \$target -Force -ErrorAction Stop"
        echo "    if (\$item.LinkType -eq 'Junction' -or \$item.LinkType -eq 'SymbolicLink') {"
        echo "        Write-Output \$item.LinkType"
        echo "    }"
        echo "} catch {"
        echo "    exit 1"
        echo "}"
    } > "$tmpfile"
    out=$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$tmpfile" 2>/dev/null)
    rc=$?
    rm -f "$tmpfile"
    if [ $rc -ne 0 ]; then
        return 1
    fi
    case "$out" in
        *Junction*|*SymbolicLink*) return 0 ;;
        *) return 1 ;;
    esac
}

# _remove_junction <target> <expected-source>
#   Removes the junction or symbolic link at <target> ONLY IF its
#   canonical target matches <expected-source>. Refuses if <target> is a
#   real directory or a link pointing somewhere else. Closes the hole
#   where a manually-re-pointed SymbolicLink could be silently followed
#   into unrelated content.
_remove_junction() {
    local target="$1"
    local expected_source="$2"
    if [ -z "$target" ] || [ -z "$expected_source" ]; then
        echo "_remove_junction: missing arguments (target='$target', expected_source='$expected_source')" >&2
        return 2
    fi

    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        # Already gone — idempotent success.
        return 0
    fi

    local target_w expected_w
    if command -v cygpath >/dev/null 2>&1; then
        target_w="$(cygpath -w "$target" 2>/dev/null)" || return 1
        expected_w="$(cygpath -w "$expected_source" 2>/dev/null)" || return 1
    else
        target_w="$target"
        expected_w="$expected_source"
    fi

    if ! command -v powershell.exe >/dev/null 2>&1; then
        # Unix host — POSIX symlink case.
        if [ -L "$target" ]; then
            local actual
            actual="$(readlink "$target")"
            if [ "$actual" != "$expected_source" ]; then
                echo "_remove_junction: $target points to $actual, not $expected_source" >&2
                return 1
            fi
            rm -f "$target"
            return $?
        fi
        echo "_remove_junction: $target is not a symbolic link" >&2
        return 1
    fi

    local tmpfile rc
    tmpfile=$(mktemp --suffix=.ps1) || return 1
    {
        echo "\$ErrorActionPreference = 'Stop'"
        echo "\$target = $(_ps_quote "$target_w")"
        echo "\$expected_source = $(_ps_quote "$expected_w")"
        echo ""
        echo "\$item = Get-Item -LiteralPath \$target -Force"
        echo "if (\$item.LinkType -eq 'Junction' -or \$item.LinkType -eq 'SymbolicLink') {"
        echo "    \$actual = @(\$item.Target)[0]"
        echo "} else {"
        echo "    throw \"Refusing: \$target is not a junction (LinkType=\$(\$item.LinkType))\""
        echo "}"
        echo ""
        echo "# Canonicalise both sides; force lowercase so a future swap of -ne"
        echo "# for the case-sensitive -cne operator stays correct on NTFS."
        echo "\$actual_full = [System.IO.Path]::GetFullPath(\$actual).ToLowerInvariant()"
        echo "\$expected_full = [System.IO.Path]::GetFullPath(\$expected_source).ToLowerInvariant()"
        echo "if (\$actual_full -ne \$expected_full) {"
        echo "    throw \"Refusing: \$target points to \$actual_full, not the expected canonical source \$expected_full\""
        echo "}"
        echo ""
        echo "# PowerShell 5.1's Remove-Item throws NullReferenceException on"
        echo "# directory junctions. [System.IO.Directory]::Delete with"
        echo "# recursive=false removes the reparse point only, leaving the"
        echo "# link target untouched."
        echo "[System.IO.Directory]::Delete(\$target, \$false)"
    } > "$tmpfile"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$tmpfile"
    rc=$?
    rm -f "$tmpfile"
    return $rc
}

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

    # Stale-link cleanup. If we have a link or junction with no reachable
    # sentinel, remove it (canonical-source-checked) before recreating.
    if [ -L "$target" ] || _is_junction "$target"; then
        _remove_junction "$target" "$source" || {
            echo "_ensure_junction: cannot reclaim stale link at $target (points elsewhere)" >&2
            return 1
        }
    fi

    # Empty real dir? Remove so junction can take its place.
    if [ -d "$target" ] && [ -z "$(ls -A "$target" 2>/dev/null)" ]; then
        rmdir "$target" 2>/dev/null
    fi

    if [ -e "$target" ]; then
        echo "Cannot create junction at $target — path exists with content." >&2
        echo "  Resolve manually: ensure $target is empty or a junction to $source." >&2
        return 1
    fi

    # Windows (Git Bash / MSYS): use PowerShell tempfile with paths baked in
    # as PowerShell single-quoted literals. The tempfile takes zero
    # arguments — bash invokes it as
    #   powershell.exe -NoProfile -ExecutionPolicy Bypass -File <tempfile>
    # to bypass MSYS path conversion at the bash↔PowerShell boundary.
    if command -v powershell.exe >/dev/null 2>&1 && { command -v cmd.exe >/dev/null 2>&1 || [ -n "$WINDIR" ]; }; then
        local target_w source_w tmpfile rc
        if command -v cygpath >/dev/null 2>&1; then
            target_w="$(cygpath -w "$target")" || return 1
            source_w="$(cygpath -w "$source")" || return 1
        else
            target_w="$(printf '%s' "$target" | sed 's|/|\\|g')"
            source_w="$(printf '%s' "$source" | sed 's|/|\\|g')"
        fi
        # mktemp --suffix is supported on Git-for-Windows 2.x but not all
        # older MSYS environments. If support for older Git installs is
        # ever needed, swap to:
        #   tmpfile=$(mktemp) && mv "$tmpfile" "$tmpfile.ps1" && tmpfile="$tmpfile.ps1"
        tmpfile=$(mktemp --suffix=.ps1) || return 1
        {
            echo "\$ErrorActionPreference = 'Stop'"
            echo "\$target = $(_ps_quote "$target_w")"
            echo "\$source = $(_ps_quote "$source_w")"
            echo 'New-Item -ItemType Junction -Path $target -Target $source | Out-Null'
        } > "$tmpfile"
        # Explicit cleanup vs trap ... RETURN: RETURN traps in bash are
        # per-function, but the launcher is sourced, helpers call helpers,
        # and a nested RETURN trap can fire while the outer PowerShell
        # call is still mid-flight in some shell configurations —
        # deleting the tempfile out from under the running interpreter.
        # Local-var + explicit rm -f after the rc capture is reliable
        # across all bash versions Git for Windows ships.
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$tmpfile"
        rc=$?
        rm -f "$tmpfile"

        if [ $rc -ne 0 ]; then
            echo "Failed to junction $target -> $source via PowerShell New-Item" >&2
            echo "  See in-flight.md 'Manual junction fallback' for the cmd.exe escape hatch." >&2
            return 1
        fi

        # Verify: re-check the sentinel after creation.
        if [ ! -f "$target/$sentinel" ]; then
            echo "Junction created at $target but sentinel '$sentinel' is not reachable through it." >&2
            echo "  See in-flight.md 'Manual junction fallback' for the cmd.exe escape hatch." >&2
            return 1
        fi

        echo "Junctioned $target -> $source"
        return 0
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

# _atomic_jq_write <ws_path> <jq_filter> [jq_args...]
#   Atomically rewrites <ws_path> with the result of running jq with the
#   given filter and args. Steps:
#     1. Validate <ws_path> is valid JSON (refuse if not — never overwrite
#        a corrupted file).
#     2. Take a .bak snapshot at <ws_path>.bak (overwrite any prior).
#     3. Run jq into a same-directory tempfile (required for atomic
#        rename on NTFS).
#     4. Validate the tempfile is valid JSON.
#     5. mv -f tempfile -> <ws_path> (atomic rename within filesystem).
#   On any failure between 2-5 the tempfile is deleted, the original is
#   intact, and <ws_path>.bak is the rollback.
_atomic_jq_write() {
    local ws="$1"
    local filter="$2"
    shift 2

    if [ ! -f "$ws" ]; then
        echo "_atomic_jq_write: workspace file $ws does not exist" >&2
        return 1
    fi
    if ! jq . "$ws" >/dev/null 2>&1; then
        echo "_atomic_jq_write: $ws is not valid JSON; refusing to overwrite" >&2
        return 1
    fi

    cp -f "$ws" "$ws.bak" || {
        echo "_atomic_jq_write: failed to take backup at $ws.bak" >&2
        return 1
    }

    local tmp
    tmp=$(mktemp --tmpdir="$(dirname "$ws")" "ws-XXXXXX.json") || {
        echo "_atomic_jq_write: failed to create tempfile in $(dirname "$ws")" >&2
        return 1
    }

    if ! jq "$@" "$filter" "$ws" > "$tmp"; then
        rm -f "$tmp"
        echo "_atomic_jq_write: jq filter failed" >&2
        return 1
    fi

    if ! jq . "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp"
        echo "_atomic_jq_write: jq output is not valid JSON; refusing rename" >&2
        return 1
    fi

    if ! mv -f "$tmp" "$ws"; then
        rm -f "$tmp"
        echo "_atomic_jq_write: rename of tempfile to $ws failed" >&2
        return 1
    fi
    return 0
}

_add_workspace_folder() {
    local folder="$1"
    _require_jq || return $?
    _ensure_workspace_file
    local ws
    ws="$(_workspace_file)"

    # Normalise both slash variants to lowercase for dupe detection.
    # tr is used (not sed) for the / <-> \ swap because single character
    # substitution sidesteps sed's backslash-escape ambiguity in shell
    # single-quoted strings.
    local fwd_lower bwd_lower
    fwd_lower="$(printf '%s' "$folder" | tr '\\\\' '/' | tr '[:upper:]' '[:lower:]')"
    bwd_lower="$(printf '%s' "$folder" | tr '/' '\\\\' | tr '[:upper:]' '[:lower:]')"

    # If either variant of the path is already present, skip.
    local already
    already="$(jq --arg fwd "$fwd_lower" --arg bwd "$bwd_lower" '
        [.folders[]? | select(
            (.path | ascii_downcase) == $fwd or
            (.path | ascii_downcase) == $bwd
        )] | length' "$ws" 2>/dev/null)"
    if [ "${already:-0}" -gt 0 ]; then
        return 0
    fi

    if _atomic_jq_write "$ws" '.folders = (.folders // []) + [{path: $p}]' --arg p "$folder"; then
        echo "Added $folder to Cursor workspace"
    else
        echo "Failed to update workspace file" >&2
        return 1
    fi
}

# _remove_workspace_folder_all_variants <folder>
#   Drops every entry in .folders whose .path matches <folder> in any
#   forward-slash, backslash, or case variant. Replaces the previous
#   exact-string-match implementation, which would leave the same
#   worktree present under a different slash style and accumulate dupes
#   over time.
_remove_workspace_folder_all_variants() {
    local folder="$1"
    _require_jq || return $?
    local ws
    ws="$(_workspace_file)"
    [ -f "$ws" ] || return 0

    # Normalise via tr (not sed) — single-char substitution avoids the
    # backslash-escape ambiguity sed has in shell single-quoted strings.
    local fwd_lower bwd_lower
    fwd_lower="$(printf '%s' "$folder" | tr '\\\\' '/' | tr '[:upper:]' '[:lower:]')"
    bwd_lower="$(printf '%s' "$folder" | tr '/' '\\\\' | tr '[:upper:]' '[:lower:]')"

    # Quick check: if no matching entry exists, no-op.
    local matches
    matches="$(jq --arg fwd "$fwd_lower" --arg bwd "$bwd_lower" '
        [.folders[]? | select(
            (.path | ascii_downcase) == $fwd or
            (.path | ascii_downcase) == $bwd
        )] | length' "$ws" 2>/dev/null)"
    if [ "${matches:-0}" -eq 0 ]; then
        return 0
    fi

    if _atomic_jq_write "$ws" '
        .folders |= map(select(
            (.path | ascii_downcase) != $fwd and
            (.path | ascii_downcase) != $bwd
        ))' --arg fwd "$fwd_lower" --arg bwd "$bwd_lower"; then
        echo "Removed $folder (and slash/case variants) from Cursor workspace"
    else
        return 1
    fi
}

# Backward-compat alias for any sourced ~/.bashrc snippets that may have
# referenced the older function name.
_remove_workspace_folder() { _remove_workspace_folder_all_variants "$@"; }

# ---------------------------------------------------------------------------
# Terminal tab title (works in Cursor / VS Code / xterm-compatible terminals)
# ---------------------------------------------------------------------------

_set_terminal_title() {
    # OSC 0 — sets icon + window title; Cursor uses this for terminal tabs.
    printf '\033]0;%s\007' "$1"
}

# ---------------------------------------------------------------------------
# Public API: start_lead / stop_lead / finish_lead
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

    # Strict resolution via _parse_kickoff (exports _PARSED_BRANCH + _PARSED_WORKTREE).
    _parse_kickoff "$name" || return $?
    local branch="$_PARSED_BRANCH"
    local worktree="$_PARSED_WORKTREE"
    local prompt_path="$AGENTFORGE_ROOT/.gauntlet/week2/kickoff/$name.md"

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

    _remove_workspace_folder_all_variants "$worktree"
    _set_terminal_title "AgentForge"

    local title_case
    title_case="$(printf '%s' "$name" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
    echo "Stopped tracking $title_case. Worktree at $worktree is untouched — run start_$name to resume."
}

# _finish_lead <name> [--force] [--keep-branch] [--no-fetch]
#   Safe teardown: removes the lead's worktree, branch, junctions, and
#   workspace JSON entry once the branch has merged into origin/master.
#   Each step refuses with a one-line error and aborts on failure; no
#   `set -e` so error routing stays explicit.
#
#   --force         Allow unmerged branch, unpushed commits, git branch -D.
#   --keep-branch   Preserve the branch (still removes worktree + junctions).
#   --no-fetch      Skip the `git fetch` step (offline use only — stale
#                   origin/master is the failure mode this guards against).
_finish_lead() {
    local name="$1"
    if [ -z "$name" ]; then
        cat >&2 <<EOF
Usage: finish_lead <name> [--force] [--keep-branch] [--no-fetch]

  <name>           Lead identifier (aria, bram, cleo, ...).
  --force          Allow unmerged branch, unpushed commits, git branch -D.
  --keep-branch    Preserve the branch (still removes worktree + junctions).
  --no-fetch       Skip 'git fetch origin' before the merged-into-master check.
                   Use only when offline; default is to fetch because stale
                   origin/master is the principal failure mode for "is it
                   actually merged?"
EOF
        return 2
    fi
    shift

    local force=0 keep_branch=0 no_fetch=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --force)        force=1; shift ;;
            --keep-branch)  keep_branch=1; shift ;;
            --no-fetch)     no_fetch=1; shift ;;
            -h|--help)      _finish_lead; return 0 ;;
            *)
                echo "finish_lead: unknown option: $1" >&2
                return 2
                ;;
        esac
    done

    # Step 1: Resolve target via _parse_kickoff.
    _parse_kickoff "$name" || return $?
    local branch="$_PARSED_BRANCH"
    local worktree="$_PARSED_WORKTREE"

    # Step 2: Refuse if standing inside the worktree being torn down.
    local cur_top cur_top_abs worktree_abs
    cur_top=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$cur_top" ]; then
        if command -v realpath >/dev/null 2>&1; then
            cur_top_abs="$(realpath -m "$cur_top" 2>/dev/null)"
            worktree_abs="$(realpath -m "$worktree" 2>/dev/null)"
        else
            cur_top_abs="$cur_top"
            worktree_abs="$worktree"
        fi
        if [ "$cur_top_abs" = "$worktree_abs" ]; then
            echo "finish_lead: refuse — current shell is inside $worktree. cd to the main checkout first." >&2
            return 1
        fi
    fi

    # Step 3: Verify path is a registered git worktree (and not the main checkout).
    local registered=0
    while IFS= read -r line; do
        case "$line" in
            "worktree "*)
                local wt_path="${line#worktree }"
                local wt_abs
                if command -v realpath >/dev/null 2>&1; then
                    wt_abs="$(realpath -m "$wt_path" 2>/dev/null)"
                else
                    wt_abs="$wt_path"
                fi
                if [ "$wt_abs" = "$worktree_abs" ]; then
                    registered=1
                fi
                ;;
        esac
    done < <(git -C "$AGENTFORGE_ROOT" worktree list --porcelain 2>/dev/null)

    if [ "$registered" -ne 1 ]; then
        echo "finish_lead: refuse — $worktree is not a registered git worktree of $AGENTFORGE_ROOT" >&2
        return 1
    fi
    local main_abs
    if command -v realpath >/dev/null 2>&1; then
        main_abs="$(realpath -m "$AGENTFORGE_ROOT" 2>/dev/null)"
    else
        main_abs="$AGENTFORGE_ROOT"
    fi
    if [ "$worktree_abs" = "$main_abs" ]; then
        echo "finish_lead: refuse — $worktree is the main checkout, not a lead worktree" >&2
        return 1
    fi

    # Step 4: Verify worktree is clean.
    local dirty
    dirty=$(git -C "$worktree" status --porcelain 2>/dev/null)
    if [ -n "$dirty" ]; then
        echo "finish_lead: refuse — $worktree has uncommitted changes:" >&2
        printf '%s\n' "$dirty" | head -10 >&2
        echo "  Commit, stash, or reset before finishing." >&2
        return 1
    fi

    # Step 5: Fetch origin (unless --no-fetch).
    if [ "$no_fetch" -ne 1 ]; then
        if ! git -C "$AGENTFORGE_ROOT" fetch origin --quiet; then
            echo "finish_lead: refuse — 'git fetch origin' failed. Use --no-fetch if offline." >&2
            return 1
        fi
    fi

    # Step 6: Verify branch is merged into origin/master.
    if ! git -C "$AGENTFORGE_ROOT" rev-parse --verify "origin/master" >/dev/null 2>&1; then
        if [ "$force" -ne 1 ]; then
            echo "finish_lead: refuse — origin/master ref not found (run a fetch). Use --force to bypass." >&2
            return 1
        fi
    elif ! git -C "$AGENTFORGE_ROOT" merge-base --is-ancestor "$branch" "origin/master" 2>/dev/null; then
        if [ "$force" -ne 1 ]; then
            local branch_sha master_sha
            branch_sha=$(git -C "$AGENTFORGE_ROOT" rev-parse --short "$branch" 2>/dev/null)
            master_sha=$(git -C "$AGENTFORGE_ROOT" rev-parse --short "origin/master" 2>/dev/null)
            echo "finish_lead: refuse — $branch ($branch_sha) is not an ancestor of origin/master ($master_sha). Use --force to bypass." >&2
            return 1
        fi
    fi

    # Step 7: Verify push state vs upstream.
    local upstream
    upstream=$(git -C "$AGENTFORGE_ROOT" rev-parse --abbrev-ref --symbolic-full-name "$branch@{u}" 2>/dev/null)
    if [ -n "$upstream" ]; then
        local unpushed
        unpushed=$(git -C "$AGENTFORGE_ROOT" log "$upstream..$branch" --oneline 2>/dev/null)
        if [ -n "$unpushed" ]; then
            if [ "$force" -ne 1 ]; then
                echo "finish_lead: refuse — $branch has unpushed commits vs upstream $upstream:" >&2
                printf '%s\n' "$unpushed" >&2
                echo "  Push them first, or pass --force to discard." >&2
                return 1
            fi
        fi
    elif git -C "$AGENTFORGE_ROOT" rev-parse --verify "origin/$branch" >/dev/null 2>&1; then
        # No configured upstream but origin/<branch> exists — compare against it.
        local unpushed
        unpushed=$(git -C "$AGENTFORGE_ROOT" log "origin/$branch..$branch" --oneline 2>/dev/null)
        if [ -n "$unpushed" ]; then
            if [ "$force" -ne 1 ]; then
                echo "finish_lead: refuse — $branch has unpushed commits vs origin/$branch:" >&2
                printf '%s\n' "$unpushed" >&2
                echo "  Push them first, or pass --force to discard." >&2
                return 1
            fi
        fi
    else
        echo "finish_lead: warning — $branch has no upstream and no origin/$branch ref; treating as local-only" >&2
    fi

    # Step 8: Junction shape — both .gauntlet and .claude must be junctions, not real dirs.
    local gauntlet_path="$worktree/.gauntlet"
    local claude_path="$worktree/.claude"
    if [ -e "$gauntlet_path" ] && ! _is_junction "$gauntlet_path"; then
        echo "finish_lead: refuse — $gauntlet_path is a real directory, not a junction. Manual review required." >&2
        return 1
    fi
    if [ -e "$claude_path" ] && ! _is_junction "$claude_path"; then
        echo "finish_lead: refuse — $claude_path is a real directory, not a junction. Manual review required." >&2
        return 1
    fi

    # Step 9: Remove both junctions (canonical-source-checked).
    if [ -e "$gauntlet_path" ] || [ -L "$gauntlet_path" ]; then
        if ! _remove_junction "$gauntlet_path" "$AGENTFORGE_ROOT/.gauntlet"; then
            echo "finish_lead: refuse — failed to remove junction $gauntlet_path" >&2
            return 1
        fi
        echo "Removed junction $gauntlet_path"
    fi
    if [ -e "$claude_path" ] || [ -L "$claude_path" ]; then
        if ! _remove_junction "$claude_path" "$AGENTFORGE_ROOT/.claude"; then
            echo "finish_lead: refuse — failed to remove junction $claude_path" >&2
            return 1
        fi
        echo "Removed junction $claude_path"
    fi

    # Step 10: git worktree remove (--force only if our --force is set).
    local wt_force_flag=""
    [ "$force" -eq 1 ] && wt_force_flag="--force"
    if ! git -C "$AGENTFORGE_ROOT" worktree remove $wt_force_flag "$worktree" 2>&1; then
        echo "finish_lead: 'git worktree remove' failed for $worktree" >&2
        return 1
    fi
    echo "Removed worktree $worktree"

    # Step 11: Branch delete.
    if [ "$keep_branch" -ne 1 ]; then
        local branch_flag="-d"
        [ "$force" -eq 1 ] && branch_flag="-D"
        if ! git -C "$AGENTFORGE_ROOT" branch "$branch_flag" "$branch" 2>&1; then
            echo "finish_lead: 'git branch $branch_flag $branch' failed" >&2
            return 1
        fi
        echo "Deleted branch $branch"
    fi

    # Step 12: Atomic workspace JSON edit.
    _remove_workspace_folder_all_variants "$worktree"

    # Step 13: Reset terminal title.
    _set_terminal_title "AgentForge"

    # Step 14: Summary.
    local title_case
    title_case="$(printf '%s' "$name" | awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}')"
    cat <<EOF

finish_lead: $title_case teardown complete.
  worktree: $worktree (removed)
  branch:   $branch $([ "$keep_branch" -eq 1 ] && echo "(preserved via --keep-branch)" || echo "(deleted)")
  junctions: .gauntlet, .claude (removed if present)
  workspace JSON: stripped of $worktree (and slash/case variants)
EOF
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

start_lead()  { _start_lead  "$@"; }
stop_lead()   { _stop_lead   "$@"; }
finish_lead() { _finish_lead "$@"; }

# snake_case (bash convention) — "$@" forwards resume flags (-c, -r, --fork)
start_aria() { _start_lead "aria" "$@"; }
start_bram() { _start_lead "bram" "$@"; }
start_cleo() { _start_lead "cleo" "$@"; }

stop_aria() { _stop_lead "aria"; }
stop_bram() { _stop_lead "bram"; }
stop_cleo() { _stop_lead "cleo"; }

finish_aria() { _finish_lead "aria" "$@"; }
finish_bram() { _finish_lead "bram" "$@"; }
finish_cleo() { _finish_lead "cleo" "$@"; }

# hyphenated aliases (matches PowerShell muscle memory)
start-aria() { _start_lead "aria" "$@"; }
start-bram() { _start_lead "bram" "$@"; }
start-cleo() { _start_lead "cleo" "$@"; }

stop-aria() { _stop_lead "aria"; }
stop-bram() { _stop_lead "bram"; }
stop-cleo() { _stop_lead "cleo"; }

finish-aria() { _finish_lead "aria" "$@"; }
finish-bram() { _finish_lead "bram" "$@"; }
finish-cleo() { _finish_lead "cleo" "$@"; }

start-lead()  { _start_lead  "$@"; }
stop-lead()   { _stop_lead   "$@"; }
finish-lead() { _finish_lead "$@"; }
