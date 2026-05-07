# Lead launchers for AgentForge parallel work.
#
# Usage — dot-source from your PowerShell profile:
#   . C:\Dev\GauntletAI\AgentForge\scripts\lead-launchers.ps1
#
# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------
# The kickoff prompt at .gauntlet/week2/kickoff/<name>.md drives BOTH the
# lead's identity AND the launcher's branch/worktree binding. The launcher
# parses these two lines out of the kickoff:
#
#   **Branch:** `agentforge/w2-hitl-extraction`
#   **Worktree:** `../AgentForge-hitl`
#
# To reassign a lead to a new workstream:
#   1. Edit .gauntlet/week2/kickoff/<name>.md — update the Branch and
#      Worktree lines (and the rest of the kickoff content)
#   2. Edit .gauntlet/week2/in-flight.md — update the Leads table row
#   3. (optional) Finish-<Name> if you're done with the old worktree
#   4. Next call to Start-<Name> creates the new worktree if missing
#
# ---------------------------------------------------------------------------
# Cursor workspace integration
# ---------------------------------------------------------------------------
# Start-<Name> adds the lead's worktree to a multi-root Cursor workspace.
# Stop-<Name> removes it. Default workspace file lives at
# ${AGENTFORGE_PARENT}/AgentForge.code-workspace; override with the
# environment variable $env:CURSOR_WORKSPACE_FILE.
#
# Cursor watches the workspace file and updates the file explorer live.
# Sometimes a Ctrl+Shift+P → "Reload Window" is needed for changes to take.
#
# Start-<Name> also sets the integrated terminal tab title to the lead's
# name (Aria, Bram, …) so you can see which lead a tab is running at a glance.
# Stop-<Name> resets the title to "AgentForge".
#
# Functions:
#   Start-Lead -Name <name>   # generic launcher
#   Start-Aria / Start-Bram / Start-Cleo
#   Stop-Lead -Name <name>    # generic stopper (workspace untrack + title reset)
#   Stop-Aria  / Stop-Bram  / Stop-Cleo
#   Finish-Lead -Name <name>  # safe teardown: junctions, worktree, branch, workspace
#   Finish-Aria / Finish-Bram / Finish-Cleo

$AgentForgeRoot   = "C:\Dev\GauntletAI\AgentForge"
$AgentForgeParent = Split-Path $AgentForgeRoot -Parent

# ---------------------------------------------------------------------------
# Kickoff parsing
# ---------------------------------------------------------------------------

function _Get-KickoffField {
    param(
        [Parameter(Mandatory=$true)][string]$Field,
        [Parameter(Mandatory=$true)][string]$PromptPath
    )
    $pattern = "^\*\*$Field`:\*\* ``([^``]*)``"
    foreach ($line in Get-Content $PromptPath) {
        if ($line -match $pattern) { return $Matches[1] }
    }
    return $null
}

function _Resolve-WorktreePath {
    param([Parameter(Mandatory=$true)][string]$Raw)
    if ([System.IO.Path]::IsPathRooted($Raw)) { return $Raw }
    if ($Raw.StartsWith("../") -or $Raw.StartsWith("..\")) {
        return Join-Path $AgentForgeParent $Raw.Substring(3)
    }
    return Join-Path $AgentForgeParent $Raw
}

# Resolve a Python interpreter on PATH. Returns a 2-element @(exe, prefixArgs)
# pair: prefixArgs is non-empty only for the Windows py launcher (which needs
# `-3` to pick a Python 3 interpreter). Returns $null if no interpreter is
# available. Used by _Render-KickoffIfYaml and the Finish-Lead phase
# transition step — both fall through to a warning and skip the YAML work
# when Python is missing, rather than failing the calling operation.
function _Resolve-Python {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return @($cmd.Source, @()) }
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd) { return @($cmd.Source, @()) }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return @($cmd.Source, @('-3')) }
    return $null
}

# _Render-KickoffIfYaml -Name <name>
#   If a YAML config exists at .gauntlet\week2\kickoff\<name>.yml, invoke
#   scripts\render_kickoff.py to render it to <name>.md before parsing.
#   No-op if the YAML is absent (legacy hand-authored kickoff) so existing
#   leads keep working unchanged.
#
#   Always returns void. Render failures are warnings, not errors — the
#   caller falls through to the existing .md if it exists. Same
#   backward-compatibility guarantee as the bash launcher.
function _Render-KickoffIfYaml {
    param([Parameter(Mandatory=$true)][string]$Name)

    $yml = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.yml"
    if (-not (Test-Path -LiteralPath $yml)) { return }

    $py = _Resolve-Python
    if ($null -eq $py) {
        Write-Warning "_Render-KickoffIfYaml: python not on PATH; skipping render of $yml"
        return
    }
    $pyExe    = $py[0]
    $pyPrefix = $py[1]

    $script = Join-Path $AgentForgeRoot "scripts\render_kickoff.py"
    $argList = @()
    if ($pyPrefix.Count -gt 0) { $argList += $pyPrefix }
    $argList += @($script, "render", $Name)

    # Capture combined stdout+stderr via 2>&1 redirection. render_kickoff
    # writes its status line ("rendered <yml> -> <md> ...") to stderr;
    # surface it to the caller so they can see which phase rendered.
    $output = & $pyExe @argList 2>&1
    $rc = $LASTEXITCODE
    if ($output) {
        foreach ($line in @($output)) {
            Write-Host $line
        }
    }
    if ($rc -ne 0) {
        Write-Warning "_Render-KickoffIfYaml: render failed for $Name (exit $rc); falling back to existing .md"
    }
}

# Hardened kickoff resolver used by Start-Lead and Finish-Lead. Validates
# the branch and worktree fields with strict regex and resolves the
# worktree to an absolute Windows path. Returns a hashtable with Branch,
# Worktree (raw, as written), and AbsWorktree (canonical absolute).
#
# If a YAML config exists at <kickoff>\<name>.yml, the function first
# re-renders <name>.md from it (via render_kickoff.py) so the parsed
# metadata reflects the active phase. Legacy leads with no YAML keep
# working against their hand-authored .md.
#
# Throws (rather than returning $null) on any validation failure — this
# is the safe-resolution helper for destructive operations.
function _Parse-Kickoff {
    param([Parameter(Mandatory=$true)][string]$Name)

    # Render YAML -> MD first if a YAML config exists; falls through silently
    # when the lead is still on the legacy hand-authored kickoff format.
    _Render-KickoffIfYaml -Name $Name

    $promptPath = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.md"
    if (-not (Test-Path -LiteralPath $promptPath)) {
        throw "Kickoff prompt not found at $promptPath."
    }

    $lines = Get-Content -LiteralPath $promptPath

    $branchPattern   = "^\*\*Branch:\*\* ``([^``]*)``"
    $worktreePattern = "^\*\*Worktree:\*\* ``([^``]*)``"

    $branchMatches   = @()
    $worktreeMatches = @()
    foreach ($line in $lines) {
        if ($line -match $branchPattern)   { $branchMatches   += $Matches[1] }
        if ($line -match $worktreePattern) { $worktreeMatches += $Matches[1] }
    }

    if ($branchMatches.Count -eq 0)   { throw "Kickoff $promptPath has no **Branch:** line." }
    if ($branchMatches.Count -gt 1)   { throw "Kickoff $promptPath has $($branchMatches.Count) **Branch:** lines (expected exactly 1)." }
    if ($worktreeMatches.Count -eq 0) { throw "Kickoff $promptPath has no **Worktree:** line." }
    if ($worktreeMatches.Count -gt 1) { throw "Kickoff $promptPath has $($worktreeMatches.Count) **Worktree:** lines (expected exactly 1)." }

    $branch   = $branchMatches[0]
    $worktree = $worktreeMatches[0]

    if ($branch -notmatch '^[A-Za-z0-9/_.-]+$') {
        throw "Kickoff ${promptPath}: branch '$branch' contains illegal characters (allowed: A-Z a-z 0-9 / _ . -)."
    }

    # Strict shape: relative-up-one-then-single-segment. Forbids absolute
    # paths, embedded '..', sideways traversal, nested directories, and
    # shell metacharacters. Side-effect: makes GetFullPath() canonical
    # without ambiguity.
    if ($worktree -notmatch '^\.\./[A-Za-z0-9._-]+$') {
        throw "Kickoff ${promptPath}: worktree '$worktree' must match '^\.\./[A-Za-z0-9._-]+\$' (single sibling segment)."
    }

    # Resolve relative to AgentForgeRoot — '..' walks up to the parent.
    $combined    = Join-Path $AgentForgeRoot $worktree
    $absWorktree = [System.IO.Path]::GetFullPath($combined)

    return @{
        Branch      = $branch
        Worktree    = $worktree
        AbsWorktree = $absWorktree
    }
}

# ---------------------------------------------------------------------------
# Cross-worktree directory junctions
# ---------------------------------------------------------------------------
# Two repo-root directories are gitignored AND need to be visible from every
# worktree:
#
#   .gauntlet\  cohort-property PRDs, personal stories, handoffs, kickoff
#               prompts, audits. Cross-lead coordination state lives here.
#   .claude\    agents\<name>.md (drives the agent badge in Cursor's
#               terminal), skills\<name>\SKILL.md (drives /aria, /bram,
#               /cleo and other slash commands), settings.json, hooks.
#
# Because git worktrees only inherit *tracked* content, a fresh worktree
# has neither directory. Solution: a directory junction from
# <worktree>\<dir> -> <main checkout>\<dir>. Single source of truth on
# disk; edits in any worktree are immediately visible everywhere; nothing
# leaks to public mirrors; no manual sync.
#
# Teardown: don't `rmdir` junctions by hand — use Finish-<Name>, which
# verifies junction identity (LinkType + canonical target match) before
# removal so you can never accidentally delete the canonical content
# under the main checkout.

# Returns $true if $Path exists and is a Junction or SymbolicLink.
# Returns $false for missing paths, real directories, or files.
function _Test-Junction {
    param([Parameter(Mandatory=$true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $false }
    return ($item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink')
}

# Removes the junction at $Target only if it points to $ExpectedSource.
# Canonicalises both sides via GetFullPath + ToLowerInvariant before
# comparing — Windows paths are case-insensitive, and forcing lowercase
# makes the invariant resilient to anyone later swapping the comparison
# operator. Throws on any mismatch; the caller's destructive flow halts
# rather than silently deleting the wrong link.
function _Remove-Junction {
    param(
        [Parameter(Mandatory=$true)][string]$Target,
        [Parameter(Mandatory=$true)][string]$ExpectedSource
    )

    if (-not (Test-Path -LiteralPath $Target)) {
        return  # Already gone, nothing to do.
    }

    $item = Get-Item -LiteralPath $Target -Force -ErrorAction Stop

    if ($item.LinkType -ne 'Junction' -and $item.LinkType -ne 'SymbolicLink') {
        throw "Refusing: $Target is not a junction (LinkType=$($item.LinkType))."
    }

    # Junction Target ships as an array on PS 5+.
    $actual = @($item.Target)[0]
    if ([string]::IsNullOrWhiteSpace($actual)) {
        throw "Refusing: $Target is a link with no resolvable target."
    }

    $actualFull   = [System.IO.Path]::GetFullPath($actual).ToLowerInvariant()
    $expectedFull = [System.IO.Path]::GetFullPath($ExpectedSource).ToLowerInvariant()

    if ($actualFull -ne $expectedFull) {
        throw "Refusing: $Target points to '$actualFull', not the expected canonical source '$expectedFull'."
    }

    # Use [System.IO.Directory]::Delete on the reparse point. This calls
    # the Win32 RemoveDirectory API, which removes the junction's
    # reparse point only — the link target is never followed or
    # touched. PowerShell 5.1's Remove-Item prompts interactively on
    # junctions in NonInteractive sessions even with -Force; this
    # bypasses that by going through .NET directly.
    [System.IO.Directory]::Delete($Target)
}

function _Ensure-Junction {
    param(
        [Parameter(Mandatory=$true)][string]$Target,
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Sentinel
    )

    # Sentinel-file check: if a known file is readable through $Target,
    # the junction is already in place.
    $sentinelPath = Join-Path $Target $Sentinel
    if (Test-Path -LiteralPath $sentinelPath) { return }

    # Existing path — figure out what's there and whether we can replace it.
    if (Test-Path -LiteralPath $Target) {
        if (_Test-Junction -Path $Target) {
            # Stale junction (sentinel unreachable). Remove via the
            # identity-checked helper before recreating. If the link
            # points at something we don't own, _Remove-Junction will
            # throw and abort the recreate.
            try {
                _Remove-Junction -Target $Target -ExpectedSource $Source
            } catch {
                Write-Error "Cannot recreate junction at ${Target}: $_"
                return
            }
        } else {
            $isEmpty = -not (Get-ChildItem -Path $Target -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($isEmpty) {
                Remove-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue
            } else {
                Write-Warning "Cannot create junction at ${Target} - path exists with content."
                Write-Warning "Resolve manually: ensure ${Target} is empty or a junction to ${Source}."
                return
            }
        }
    }

    # Create directory junction (no admin needed). Use the native
    # New-Item cmdlet — no cmd.exe shell-out, no quoting hazards.
    try {
        New-Item -ItemType Junction -Path $Target -Target $Source -ErrorAction Stop | Out-Null
    } catch {
        Write-Error "Failed to junction ${Target} -> ${Source}: $_"
        return
    }

    # Verify the junction is reachable via the sentinel before declaring
    # success. If the link was created but the sentinel still isn't
    # readable, something is wrong with the source — bail loudly.
    if (-not (Test-Path -LiteralPath $sentinelPath)) {
        Write-Error "Junction created at ${Target} but sentinel ${Sentinel} is not reachable. Manual fallback: open a real cmd.exe (Win+R -> cmd) and run 'mklink /J `"${Target}`" `"${Source}`"'."
        return
    }

    Write-Host "Junctioned ${Target} -> ${Source}" -ForegroundColor Cyan
}

function _Ensure-GauntletJunction {
    param([Parameter(Mandatory=$true)][string]$WorktreePath)
    _Ensure-Junction `
        -Target   (Join-Path $WorktreePath ".gauntlet") `
        -Source   (Join-Path $AgentForgeRoot ".gauntlet") `
        -Sentinel "week2\in-flight.md"
}

function _Ensure-ClaudeJunction {
    param([Parameter(Mandatory=$true)][string]$WorktreePath)
    _Ensure-Junction `
        -Target   (Join-Path $WorktreePath ".claude") `
        -Source   (Join-Path $AgentForgeRoot ".claude") `
        -Sentinel "agents\gauntlet-team-lead.md"
}

# ---------------------------------------------------------------------------
# Cursor workspace JSON management
# ---------------------------------------------------------------------------

function _Get-WorkspaceFile {
    if ($env:CURSOR_WORKSPACE_FILE) { return $env:CURSOR_WORKSPACE_FILE }
    return Join-Path $AgentForgeParent "AgentForge.code-workspace"
}

function _Ensure-WorkspaceFile {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) { return }
    $initial = [PSCustomObject]@{
        folders = @(
            [PSCustomObject]@{ path = $AgentForgeRoot }
        )
        settings = [PSCustomObject]@{}
    }
    $initial | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Path -Encoding UTF8
    Write-Host "Created Cursor workspace at $Path" -ForegroundColor Cyan
}

# Atomic write of the workspace JSON file:
#  1. Validate input file (if it exists) is parseable JSON. Refuse if not
#     — never overwrite a corrupted file with more corruption.
#  2. Snapshot to .bak (overwriting any prior backup).
#  3. Serialize new content to a temp file in the same directory.
#  4. Validate the temp file parses as JSON.
#  5. Move-Item -Force (atomic rename within the same filesystem).
#  6. On any failure between 2 and 5: tempfile is cleaned up; original is
#     intact; .bak is the rollback.
function _Write-WorkspaceFileAtomic {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)]$Content
    )

    $parent = [System.IO.Path]::GetDirectoryName($Path)
    if ([string]::IsNullOrEmpty($parent) -or -not (Test-Path -LiteralPath $parent)) {
        throw "Workspace parent directory does not exist: $parent"
    }

    if (Test-Path -LiteralPath $Path) {
        try {
            Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json | Out-Null
        } catch {
            throw "Refusing to overwrite $Path - file is not valid JSON. Fix or delete it manually."
        }
        # Take a backup snapshot. -Force overwrites any prior .bak.
        Copy-Item -LiteralPath $Path -Destination ($Path + '.bak') -Force -ErrorAction Stop
    }

    $tmp = Join-Path $parent ([System.IO.Path]::GetRandomFileName())
    try {
        $json = $Content | ConvertTo-Json -Depth 10
        Set-Content -LiteralPath $tmp -Value $json -Encoding UTF8 -ErrorAction Stop

        # Validate temp file parses as JSON before promoting it.
        try {
            Get-Content -Raw -LiteralPath $tmp | ConvertFrom-Json | Out-Null
        } catch {
            throw "Generated workspace JSON failed validation: $_"
        }

        Move-Item -LiteralPath $tmp -Destination $Path -Force -ErrorAction Stop
        $tmp = $null  # Successfully moved; nothing to clean up.
    } finally {
        if ($null -ne $tmp -and (Test-Path -LiteralPath $tmp)) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }
}

function _Add-WorkspaceFolder {
    param([string]$WorkspacePath, [string]$FolderPath)
    _Ensure-WorkspaceFile -Path $WorkspacePath
    $ws = Get-Content -Raw -LiteralPath $WorkspacePath | ConvertFrom-Json
    if (-not $ws.folders) {
        $ws | Add-Member -NotePropertyName folders -NotePropertyValue @() -Force
    }

    # Case-insensitive, slash-variant-tolerant dedup. Prevents a folder
    # being added under both AgentForge-eval and AgentForge-eval/, or
    # under both forward- and back-slash forms.
    $fwdLower = ($FolderPath -replace '\\','/').ToLowerInvariant()
    $bwdLower = ($FolderPath -replace '/','\').ToLowerInvariant()
    $existing = @($ws.folders | Where-Object {
        $p = ([string]$_.path).ToLowerInvariant()
        $p -eq $fwdLower -or $p -eq $bwdLower
    })
    if ($existing.Count -gt 0) { return }

    $ws.folders = @($ws.folders) + @([PSCustomObject]@{ path = $FolderPath })
    _Write-WorkspaceFileAtomic -Path $WorkspacePath -Content $ws
    Write-Host "Added $FolderPath to Cursor workspace" -ForegroundColor Cyan
}

# Removes ALL entries matching the target path in either slash variant
# (case-insensitive). Cleans up the historic dupes the launcher used to
# create before the case-/slash-aware dedup was added to _Add-WorkspaceFolder.
function _Remove-WorkspaceFolderAllVariants {
    param([string]$WorkspacePath, [string]$FolderPath)
    if (-not (Test-Path -LiteralPath $WorkspacePath)) { return }
    $ws = Get-Content -Raw -LiteralPath $WorkspacePath | ConvertFrom-Json
    if (-not $ws.folders) { return }

    $fwdLower = ($FolderPath -replace '\\','/').ToLowerInvariant()
    $bwdLower = ($FolderPath -replace '/','\').ToLowerInvariant()

    $newFolders = @($ws.folders | Where-Object {
        $p = ([string]$_.path).ToLowerInvariant()
        $p -ne $fwdLower -and $p -ne $bwdLower
    })

    # Short-circuit if nothing matched — no point rewriting the file.
    if ($newFolders.Count -eq @($ws.folders).Count) { return }

    $ws.folders = $newFolders
    _Write-WorkspaceFileAtomic -Path $WorkspacePath -Content $ws
    Write-Host "Removed $FolderPath (all variants) from Cursor workspace" -ForegroundColor Cyan
}

# Backward-compat alias for the simpler exact-match remover. Delegates
# to the all-variants version since stale callers should benefit from
# the slash-tolerant matching anyway.
function _Remove-WorkspaceFolder {
    param([string]$WorkspacePath, [string]$FolderPath)
    _Remove-WorkspaceFolderAllVariants -WorkspacePath $WorkspacePath -FolderPath $FolderPath
}

# ---------------------------------------------------------------------------
# Terminal tab title (works in Cursor / VS Code / Windows Terminal)
# ---------------------------------------------------------------------------

function _Set-TerminalTitle {
    param([Parameter(Mandatory=$true)][string]$Title)
    # PowerShell host title (visible in Windows Terminal / classic console).
    $Host.UI.RawUI.WindowTitle = $Title
    # OSC 0 escape sequence — Cursor / VS Code respect this for tab titles.
    # Use [char] casts for PowerShell 5.1 compatibility (no `e support).
    [Console]::Write("$([char]27)]0;$Title$([char]7)")
}

# ---------------------------------------------------------------------------
# Public API: Start-Lead / Stop-Lead / Finish-Lead
# ---------------------------------------------------------------------------

function Start-Lead {
    <#
    .SYNOPSIS
    Launch a Week-2 lead session in its dedicated worktree.

    .DESCRIPTION
    Creates the worktree if missing, junctions .gauntlet/ and .claude/ from
    the main checkout, registers the worktree with the Cursor multi-root
    workspace, titles the terminal tab, and launches Claude with the
    lead-specific --agent flag.

    Default behavior is a fresh session seeded with the lead's kickoff
    prompt. Use -Continue to resume the most recent session, or -Resume to
    open Claude's session picker.

    .PARAMETER Name
    Lead identifier (aria, bram, cleo, ...).

    .PARAMETER Continue
    Resume the most recent conversation in the lead's worktree.

    .PARAMETER Resume
    Open Claude's resume picker. Use -Search to pre-filter the picker.

    .PARAMETER Search
    Optional search term passed to --resume to filter the picker.

    .PARAMETER Fork
    When resuming, fork to a new session ID so the original stays intact.
    Only meaningful with -Continue or -Resume.

    .EXAMPLE
    Start-Bram                         # fresh, kickoff loaded
    Start-Bram -Continue               # resume most recent session
    Start-Bram -Resume                 # open picker
    Start-Bram -Resume -Search "eval"  # picker filtered to "eval"
    Start-Bram -Continue -Fork         # resume + fork (preserve old)
    #>
    param(
        [Parameter(Mandatory=$true, Position=0)][string]$Name,
        [switch]$Continue,
        [switch]$Resume,
        [string]$Search = "",
        [switch]$Fork
    )

    if ($Continue -and $Resume) {
        Write-Error "Use only one of -Continue or -Resume."
        return
    }

    try {
        $kickoff = _Parse-Kickoff -Name $Name
    } catch {
        Write-Error $_
        return
    }
    $branch     = $kickoff.Branch
    $worktree   = $kickoff.AbsWorktree
    $promptPath = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.md"

    if (-not (Test-Path -LiteralPath $worktree)) {
        Write-Host "Creating worktree at $worktree on branch $branch..." -ForegroundColor Cyan
        git -C $AgentForgeRoot rev-parse --verify $branch *>$null
        if ($LASTEXITCODE -eq 0) {
            git -C $AgentForgeRoot worktree add $worktree $branch
        } else {
            git -C $AgentForgeRoot worktree add $worktree -b $branch
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Error "git worktree add failed (exit $LASTEXITCODE)"
            return
        }
    }

    # Ensure .gauntlet/ junction so handoffs / in-flight / kickoff prompts
    # are visible from inside the worktree. Idempotent — runs every time
    # in case the worktree was created manually without the launcher.
    _Ensure-GauntletJunction -WorktreePath $worktree

    # Ensure .claude/ junction so the per-lead agent file
    # (.claude\agents\<name>.md) and slash-command skills
    # (.claude\skills\<name>\SKILL.md) are findable from inside the
    # worktree. Without this, --agent <name> below would fail or fall
    # back to the wrong identity.
    _Ensure-ClaudeJunction -WorktreePath $worktree

    # Add to Cursor workspace (idempotent, slash-/case-tolerant).
    $workspacePath = _Get-WorkspaceFile
    _Add-WorkspaceFolder -WorkspacePath $workspacePath -FolderPath $worktree

    # Title the terminal tab so you can see which lead is running here.
    $titleCase = (Get-Culture).TextInfo.ToTitleCase($Name.ToLower())
    _Set-TerminalTitle -Title $titleCase

    # The --agent flag value is what Cursor's terminal renders as the
    # session badge in the top-right (e.g. "bram" instead of
    # "gauntlet-team-lead"). Each lead has a thin agent file at
    # .claude\agents\<name>.md that inherits gauntlet-team-lead's hard
    # rules and overrides only the identity line. If the lead-specific
    # file is missing, fall back to gauntlet-team-lead so the launcher
    # still works for ad-hoc lead names.
    $agentId = $Name
    $agentFile = Join-Path $AgentForgeRoot ".claude\agents\$Name.md"
    if (-not (Test-Path -LiteralPath $agentFile)) {
        Write-Warning "No agent file at .claude\agents\$Name.md - falling back to gauntlet-team-lead."
        $agentId = "gauntlet-team-lead"
    }

    # Build claude args. --teammate-mode in-process is constant; --agent picks
    # the lead identity; resume flags (if any) replace the kickoff-prompt
    # positional argument because resuming an existing conversation should
    # not re-inject the kickoff system message.
    $claudeArgs = @("--agent", $agentId, "--teammate-mode", "in-process")
    if ($Fork) { $claudeArgs += "--fork-session" }

    $prompt = Get-Content -Raw -LiteralPath $promptPath
    Push-Location $worktree
    try {
        if ($Continue) {
            $claudeArgs += "--continue"
            $modeDesc = "continue"
            if ($Fork) { $modeDesc += " +fork" }
            Write-Host "Launching Claude as $titleCase in $worktree (branch: $branch, agent: $agentId, mode: $modeDesc)..." -ForegroundColor Green
            & claude @claudeArgs
        }
        elseif ($Resume) {
            if ($Search) {
                $claudeArgs += @("--resume", $Search)
                $modeDesc = "resume picker filter='$Search'"
            } else {
                $claudeArgs += "--resume"
                $modeDesc = "resume picker"
            }
            if ($Fork) { $modeDesc += " +fork" }
            Write-Host "Launching Claude as $titleCase in $worktree (branch: $branch, agent: $agentId, mode: $modeDesc)..." -ForegroundColor Green
            & claude @claudeArgs
        }
        else {
            Write-Host "Launching Claude as $titleCase in $worktree (branch: $branch, agent: $agentId, mode: fresh)..." -ForegroundColor Green
            & claude @claudeArgs $prompt
        }
    } finally {
        Pop-Location
    }
}

function Stop-Lead {
    param([Parameter(Mandatory=$true)][string]$Name)

    try {
        $kickoff = _Parse-Kickoff -Name $Name
    } catch {
        Write-Error $_
        return
    }
    $worktree = $kickoff.AbsWorktree

    $workspacePath = _Get-WorkspaceFile
    _Remove-WorkspaceFolderAllVariants -WorkspacePath $workspacePath -FolderPath $worktree

    _Set-TerminalTitle -Title "AgentForge"

    Write-Host "Stopped tracking $Name. Worktree at $worktree is untouched - run Start-$((Get-Culture).TextInfo.ToTitleCase($Name.ToLower())) to resume." -ForegroundColor Green
}

function Finish-Lead {
    <#
    .SYNOPSIS
    Safe teardown of a lead's worktree, junctions, and branch.

    .DESCRIPTION
    Walks the safety checklist in order. Each step refuses with an
    explicit error and skips subsequent destructive steps if any prior
    step failed. The full checklist:

      1. Resolve target via _Parse-Kickoff (validated branch + abs worktree).
      2. Refuse if standing inside the worktree being torn down.
      3. Verify path is a registered git worktree (not the main checkout).
      4. Verify worktree is clean (no uncommitted/untracked content).
      5. Fetch origin (unless -NoFetch) so merge/push checks see fresh state.
      6. Verify branch is merged into origin/master (override with -Force).
      7. Verify branch is fully pushed to its upstream / origin (override
         with -Force; warns if branch has no upstream and no origin/<branch>).
      8. Verify .gauntlet and .claude inside the worktree are junctions
         (not real directories — that footgun has no -Force override).
      9. Remove the two junctions via identity-checked _Remove-Junction.
     10. git worktree remove (adds --force only if -Force was passed and
         all prior safety checks have already passed).
     11. git branch -d (or -D with -Force); skipped with -KeepBranch.
     12. Atomic edit of the Cursor workspace JSON, dropping all variants.
     13. Reset terminal title.
     14. Print a one-block summary.

    .PARAMETER Name
    Lead identifier (aria, bram, cleo, ...).

    .PARAMETER Force
    Allow unmerged branch, unpushed commits, git branch -D, and
    git worktree remove --force. Does NOT bypass the junction-not-real-dir
    refusal; that check is structural.

    .PARAMETER KeepBranch
    Tear down worktree + junctions + workspace entry, but preserve the
    branch.

    .PARAMETER NoFetch
    Skip the git fetch step (offline use). Without this, default behavior
    fetches before the merged/pushed checks because stale origin/master
    is the failure mode this tool is designed to prevent.

    .PARAMETER NextPhase
    After successful teardown, transition the lead to a new phase by
    promoting the handoff's "Recommended next PM prompt" into the lead's
    YAML config under this phase name. Requires -NextBranch. Mutually
    exclusive with -KeepBranch (one says delete-and-rotate, the other
    says preserve). Skipped silently with a warning if the lead has no
    YAML config (still on the legacy hand-authored kickoff).

    .PARAMETER NextBranch
    Branch for the new phase (required with -NextPhase). Used as the
    `branch` field on the new YAML phase entry; the next Start-<Lead>
    will create a worktree on this branch.

    .PARAMETER NextWorktree
    Worktree path for the new phase. Defaults to the current phase's
    worktree (i.e., reuse the same checkout location for the next phase).
    Only meaningful with -NextPhase.

    .PARAMETER NextMission
    Short mission summary for the new phase. Heuristic if omitted (first
    non-identity line of the recommended prompt). Only meaningful with
    -NextPhase.
    #>
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [switch]$Force,
        [switch]$KeepBranch,
        [switch]$NoFetch,
        [string]$NextPhase = "",
        [string]$NextBranch = "",
        [string]$NextWorktree = "",
        [string]$NextMission = ""
    )

    # --- Cross-flag validation ----------------------------------------
    # Done before any destructive step so contradictory invocations bail
    # before touching the worktree. Mirrors the bash launcher's checks.
    if ($NextPhase -and -not $NextBranch) {
        Write-Error "Finish-Lead: -NextPhase requires -NextBranch"
        return
    }
    if ($NextPhase -and $KeepBranch) {
        Write-Error "Finish-Lead: -NextPhase and -KeepBranch are contradictory (one says delete-and-rotate, the other says preserve)"
        return
    }
    if ((-not $NextPhase) -and ($NextBranch -or $NextWorktree -or $NextMission)) {
        Write-Error "Finish-Lead: -NextBranch / -NextWorktree / -NextMission only meaningful with -NextPhase"
        return
    }

    $errored = $false

    # --- Step 1: resolve target ---------------------------------------
    try {
        $kickoff = _Parse-Kickoff -Name $Name
    } catch {
        Write-Error "Step 1 (resolve kickoff): $_"
        return
    }
    $branch   = $kickoff.Branch
    $worktree = $kickoff.AbsWorktree

    # --- Step 2: refuse if standing inside the target worktree --------
    if (-not $errored) {
        $cwdToplevel = (& git -C $PWD rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and $cwdToplevel) {
            $cwdCanon  = [System.IO.Path]::GetFullPath($cwdToplevel.Trim()).TrimEnd('\','/').ToLowerInvariant()
            $treeCanon = [System.IO.Path]::GetFullPath($worktree).TrimEnd('\','/').ToLowerInvariant()
            if ($cwdCanon -eq $treeCanon) {
                Write-Error "Step 2: refusing to teardown $worktree from inside it. cd to the main checkout first ($AgentForgeRoot)."
                $errored = $true
            }
        }
    }

    # --- Step 3: verify worktree is registered (and not the main checkout) ---
    if (-not $errored) {
        $wtListLines = & git -C $AgentForgeRoot worktree list --porcelain 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Step 3: 'git worktree list --porcelain' failed."
            $errored = $true
        } else {
            $registered = @()
            foreach ($line in $wtListLines) {
                if ($line -match '^worktree\s+(.+)$') {
                    $registered += [System.IO.Path]::GetFullPath($Matches[1]).TrimEnd('\','/').ToLowerInvariant()
                }
            }
            $treeCanon = [System.IO.Path]::GetFullPath($worktree).TrimEnd('\','/').ToLowerInvariant()
            $mainCanon = [System.IO.Path]::GetFullPath($AgentForgeRoot).TrimEnd('\','/').ToLowerInvariant()
            if ($registered -notcontains $treeCanon) {
                Write-Error "Step 3: $worktree is not a registered git worktree (per 'git worktree list')."
                $errored = $true
            } elseif ($treeCanon -eq $mainCanon) {
                Write-Error "Step 3: $worktree is the main checkout. Refusing to remove."
                $errored = $true
            }
        }
    }

    # --- Step 4: verify worktree is clean -----------------------------
    if (-not $errored) {
        if (Test-Path -LiteralPath $worktree) {
            $statusOut = & git -C $worktree status --porcelain 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Error "Step 4: 'git status --porcelain' failed in $worktree."
                $errored = $true
            } elseif ($statusOut) {
                Write-Error "Step 4: worktree $worktree is not clean. Commit, stash, or reset before finishing. Output:`n$statusOut"
                $errored = $true
            }
        } else {
            Write-Error "Step 4: worktree path $worktree does not exist on disk."
            $errored = $true
        }
    }

    # --- Step 5: fetch origin ----------------------------------------
    if (-not $errored -and -not $NoFetch) {
        & git -C $AgentForgeRoot fetch origin --quiet 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Step 5: 'git fetch origin' failed. Use -NoFetch if you are offline (and accept that origin/master may be stale)."
            $errored = $true
        }
    }

    # --- Step 6: verify branch merged into origin/master --------------
    if (-not $errored) {
        $branchSha = (& git -C $AgentForgeRoot rev-parse $branch 2>$null)
        $masterSha = (& git -C $AgentForgeRoot rev-parse origin/master 2>$null)
        & git -C $AgentForgeRoot merge-base --is-ancestor $branch origin/master 2>$null
        $isMerged = ($LASTEXITCODE -eq 0)
        if (-not $isMerged) {
            if ($Force) {
                Write-Warning "Step 6: $branch ($branchSha) is NOT merged into origin/master ($masterSha). Proceeding because -Force was passed."
            } else {
                Write-Error "Step 6: $branch ($branchSha) is NOT merged into origin/master ($masterSha). Pass -Force to override."
                $errored = $true
            }
        }
    }

    # --- Step 7: verify push state -----------------------------------
    if (-not $errored) {
        $upstream = & git -C $AgentForgeRoot rev-parse --abbrev-ref --symbolic-full-name "$branch@{u}" 2>$null
        $hasUpstream = ($LASTEXITCODE -eq 0 -and $upstream)

        if ($hasUpstream) {
            $unpushed = & git -C $AgentForgeRoot log "$upstream..$branch" --oneline 2>$null
            if ($unpushed) {
                if ($Force) {
                    Write-Warning "Step 7: $branch has unpushed commits relative to $upstream. Proceeding because -Force was passed.`n$unpushed"
                } else {
                    Write-Error "Step 7: $branch has unpushed commits relative to $upstream. Pass -Force to override.`n$unpushed"
                    $errored = $true
                }
            }
        } else {
            # No upstream — try origin/<branch> as a fallback.
            & git -C $AgentForgeRoot rev-parse --verify "origin/$branch" *>$null
            if ($LASTEXITCODE -eq 0) {
                $unpushed = & git -C $AgentForgeRoot log "origin/$branch..$branch" --oneline 2>$null
                if ($unpushed) {
                    if ($Force) {
                        Write-Warning "Step 7: $branch has unpushed commits relative to origin/$branch. Proceeding because -Force was passed.`n$unpushed"
                    } else {
                        Write-Error "Step 7: $branch has unpushed commits relative to origin/$branch. Pass -Force to override.`n$unpushed"
                        $errored = $true
                    }
                }
            } else {
                Write-Warning "Step 7: $branch has no upstream and no origin/$branch ref; treating as local-only. Step 6 (merged-into-master) is the gate."
            }
        }
    }

    # --- Step 8: verify junctions are junctions (not real dirs) -------
    $gauntletPath = Join-Path $worktree ".gauntlet"
    $claudePath   = Join-Path $worktree ".claude"
    if (-not $errored) {
        if (Test-Path -LiteralPath $gauntletPath) {
            if (-not (_Test-Junction -Path $gauntletPath)) {
                Write-Error "Step 8: $gauntletPath is a real directory, not a junction. Refusing (no -Force override - that's the footgun this tool guards against). Investigate and remove manually if appropriate."
                $errored = $true
            }
        }
        if (-not $errored -and (Test-Path -LiteralPath $claudePath)) {
            if (-not (_Test-Junction -Path $claudePath)) {
                Write-Error "Step 8: $claudePath is a real directory, not a junction. Refusing (no -Force override - that's the footgun this tool guards against). Investigate and remove manually if appropriate."
                $errored = $true
            }
        }
    }

    # --- Step 9: remove junctions (identity-checked) ------------------
    if (-not $errored) {
        $gauntletSrc = Join-Path $AgentForgeRoot ".gauntlet"
        $claudeSrc   = Join-Path $AgentForgeRoot ".claude"
        try {
            _Remove-Junction -Target $gauntletPath -ExpectedSource $gauntletSrc
        } catch {
            Write-Error "Step 9 (.gauntlet): $_"
            $errored = $true
        }
        if (-not $errored) {
            try {
                _Remove-Junction -Target $claudePath -ExpectedSource $claudeSrc
            } catch {
                Write-Error "Step 9 (.claude): $_"
                $errored = $true
            }
        }
    }

    # --- Step 10: git worktree remove --------------------------------
    if (-not $errored) {
        if ($Force) {
            & git -C $AgentForgeRoot worktree remove --force $worktree
        } else {
            & git -C $AgentForgeRoot worktree remove $worktree
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Step 10: 'git worktree remove' failed (exit $LASTEXITCODE)."
            $errored = $true
        }
    }

    # --- Step 11: branch delete --------------------------------------
    $branchDeleted = $false
    if (-not $errored -and -not $KeepBranch) {
        if ($Force) {
            & git -C $AgentForgeRoot branch -D $branch
        } else {
            & git -C $AgentForgeRoot branch -d $branch
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Step 11: 'git branch -d $branch' failed (exit $LASTEXITCODE). Worktree removed; branch preserved."
            $errored = $true
        } else {
            $branchDeleted = $true
        }
    }

    # --- Step 12: workspace JSON edit (always attempt) ----------------
    # Best-effort even on prior errors so the file doesn't keep stale
    # entries pointing at a worktree we already cleaned up.
    $workspacePath = _Get-WorkspaceFile
    try {
        _Remove-WorkspaceFolderAllVariants -WorkspacePath $workspacePath -FolderPath $worktree
    } catch {
        Write-Error "Step 12 (workspace JSON edit): $_"
    }

    # --- Step 13: terminal title --------------------------------------
    _Set-TerminalTitle -Title "AgentForge"

    # --- Step 14: summary --------------------------------------------
    Write-Host ""
    Write-Host "=== Finish-Lead summary ($Name) ===" -ForegroundColor Cyan
    Write-Host ("  Worktree:        {0}" -f $worktree)
    Write-Host ("  Branch:          {0}" -f $branch)
    if ($errored) {
        Write-Host "  Status:          INCOMPLETE (see errors above)" -ForegroundColor Yellow
    } else {
        Write-Host "  Junctions:       removed (.gauntlet, .claude)"
        Write-Host "  Worktree state:  removed via 'git worktree remove'"
        if ($KeepBranch) {
            Write-Host "  Branch state:    preserved (-KeepBranch)"
        } elseif ($branchDeleted) {
            Write-Host ("  Branch state:    deleted (git branch {0} {1})" -f ($(if ($Force) {'-D'} else {'-d'})), $branch)
        }
        Write-Host "  Workspace JSON:  cleaned up (all path variants)"
        Write-Host "  Status:          OK" -ForegroundColor Green
    }
    Write-Host ""

    # --- Step 15 (optional): phase transition -------------------------
    # Mirrors the bash launcher: if -NextPhase was passed, invoke
    # render_kickoff.py to:
    #   - read the lead's handoff for the "Recommended next PM prompt" section
    #   - mark the just-finished phase complete in <name>.yml
    #   - add the new phase as active with the extracted prompt as first_task
    #   - re-render <name>.md so the next Start-<Lead> picks up the new phase
    # The transition is a separate step from teardown so a transition failure
    # doesn't undo the cleanup that already succeeded.
    if ($NextPhase) {
        $yml = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.yml"
        if (-not (Test-Path -LiteralPath $yml)) {
            $msg = @(
                "",
                "Finish-Lead: -NextPhase requires a YAML config at $yml.",
                "  This lead is still on the legacy hand-authored kickoff format.",
                "  Bootstrap with:",
                "    python `"$AgentForgeRoot\scripts\render_kickoff.py`" init-from-md $Name --current-phase-name <prev-phase>",
                "  Then re-run Finish-Lead -NextPhase. Teardown completed cleanly; only the",
                "  phase transition step was skipped."
            ) -join "`n"
            Write-Error $msg
            return
        }

        $py = _Resolve-Python
        if ($null -eq $py) {
            Write-Error "Finish-Lead: python not on PATH; cannot run phase transition. Teardown completed."
            return
        }
        $pyExe    = $py[0]
        $pyPrefix = $py[1]

        $script = Join-Path $AgentForgeRoot "scripts\render_kickoff.py"
        $transArgs = @()
        if ($pyPrefix.Count -gt 0) { $transArgs += $pyPrefix }
        $transArgs += @($script, "transition", $Name,
                        "--to", $NextPhase,
                        "--branch", $NextBranch)
        if ($NextWorktree) { $transArgs += @("--worktree", $NextWorktree) }
        if ($NextMission)  { $transArgs += @("--mission",  $NextMission) }

        $titleCase = (Get-Culture).TextInfo.ToTitleCase($Name.ToLower())
        Write-Host ""
        Write-Host "Finish-Lead: transitioning $titleCase to $NextPhase ..." -ForegroundColor Cyan
        $transOutput = & $pyExe @transArgs 2>&1
        $transRc = $LASTEXITCODE
        if ($transOutput) {
            foreach ($line in @($transOutput)) {
                Write-Host $line
            }
        }
        if ($transRc -eq 0) {
            $renderedMd = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.md"
            Write-Host ("  YAML updated: {0}" -f $yml)
            Write-Host ("  Rendered:    {0}" -f $renderedMd)
            Write-Host ("  Next:        Start-{0} (will create the new {1} worktree)" -f $titleCase, $NextBranch)
        } else {
            Write-Error "Finish-Lead: transition step failed (exit $transRc). Teardown completed cleanly; YAML may be unchanged."
            return
        }
    }
}

# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

function Get-Leads {
    $kickoffDir = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff"
    if (-not (Test-Path -LiteralPath $kickoffDir)) {
        Write-Error "No kickoff directory at $kickoffDir"
        return
    }
    Get-ChildItem -Path $kickoffDir -Filter "*.md" -File | ForEach-Object {
        $branch      = _Get-KickoffField -Field "Branch"   -PromptPath $_.FullName
        $worktreeRaw = _Get-KickoffField -Field "Worktree" -PromptPath $_.FullName
        $worktree    = if ($worktreeRaw) { _Resolve-WorktreePath -Raw $worktreeRaw } else { $null }
        [PSCustomObject]@{
            Lead     = $_.BaseName
            Branch   = $branch
            Worktree = $worktreeRaw
            Exists   = if ($worktree) { Test-Path -LiteralPath $worktree } else { $false }
        }
    } | Format-Table -AutoSize
}

# @args splat forwards switch parameters (-Continue, -Resume, -Search, -Fork)
# from convenience wrappers through to Start-Lead.
function Start-Aria { Start-Lead -Name "aria" @args }
function Start-Bram { Start-Lead -Name "bram" @args }
function Start-Cleo { Start-Lead -Name "cleo" @args }

function Stop-Aria { Stop-Lead -Name "aria" }
function Stop-Bram { Stop-Lead -Name "bram" }
function Stop-Cleo { Stop-Lead -Name "cleo" }

# @args splat forwards -Force / -KeepBranch / -NoFetch to Finish-Lead.
function Finish-Aria { Finish-Lead -Name "aria" @args }
function Finish-Bram { Finish-Lead -Name "bram" @args }
function Finish-Cleo { Finish-Lead -Name "cleo" @args }
