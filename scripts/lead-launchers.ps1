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
#   3. (optional) git worktree remove <old-worktree> if you're done with it
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
# WARNING: before `git worktree remove <worktree>`, remove BOTH junctions
# first to avoid any risk of recursing into the canonical content:
#   cmd /c rmdir <worktree>\.gauntlet
#   cmd /c rmdir <worktree>\.claude
# (non-recursive rmdir on a junction removes only the junction, not the
# target — but `git worktree remove` may still recurse, so unjunction first.)

function _Ensure-Junction {
    param(
        [Parameter(Mandatory=$true)][string]$Target,
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Sentinel
    )

    # Sentinel-file check: if a known file is readable through $Target,
    # the junction is already in place.
    $sentinelPath = Join-Path $Target $Sentinel
    if (Test-Path $sentinelPath) { return }

    # Empty dir? Remove so junction can take its place.
    if (Test-Path $Target) {
        $isEmpty = -not (Get-ChildItem -Path $Target -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($isEmpty) {
            Remove-Item $Target -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning "Cannot create junction at ${Target} - path exists with content."
            Write-Warning "Resolve manually: ensure ${Target} is empty or a junction to ${Source}."
            return
        }
    }

    # Create directory junction (no admin needed). cmd /c is the simplest path.
    & cmd /c mklink /J "`"$Target`"" "`"$Source`"" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to junction ${Target} -> ${Source} via mklink /J (exit $LASTEXITCODE)"
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
    if (Test-Path $Path) { return }
    $initial = [PSCustomObject]@{
        folders = @(
            [PSCustomObject]@{ path = $AgentForgeRoot }
        )
        settings = [PSCustomObject]@{}
    }
    $initial | ConvertTo-Json -Depth 10 | Set-Content $Path -Encoding UTF8
    Write-Host "Created Cursor workspace at $Path" -ForegroundColor Cyan
}

function _Add-WorkspaceFolder {
    param([string]$WorkspacePath, [string]$FolderPath)
    _Ensure-WorkspaceFile -Path $WorkspacePath
    $ws = Get-Content -Raw $WorkspacePath | ConvertFrom-Json
    if (-not $ws.folders) {
        $ws | Add-Member -NotePropertyName folders -NotePropertyValue @() -Force
    }
    $existing = @($ws.folders | Where-Object { $_.path -eq $FolderPath })
    if ($existing.Count -gt 0) { return }
    $ws.folders = @($ws.folders) + @([PSCustomObject]@{ path = $FolderPath })
    $ws | ConvertTo-Json -Depth 10 | Set-Content $WorkspacePath -Encoding UTF8
    Write-Host "Added $FolderPath to Cursor workspace" -ForegroundColor Cyan
}

function _Remove-WorkspaceFolder {
    param([string]$WorkspacePath, [string]$FolderPath)
    if (-not (Test-Path $WorkspacePath)) { return }
    $ws = Get-Content -Raw $WorkspacePath | ConvertFrom-Json
    if (-not $ws.folders) { return }
    $newFolders = @($ws.folders | Where-Object { $_.path -ne $FolderPath })
    $ws.folders = $newFolders
    $ws | ConvertTo-Json -Depth 10 | Set-Content $WorkspacePath -Encoding UTF8
    Write-Host "Removed $FolderPath from Cursor workspace" -ForegroundColor Cyan
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
# Public API: Start-Lead / Stop-Lead
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

    $promptPath = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.md"
    if (-not (Test-Path $promptPath)) {
        Write-Error "Kickoff prompt not found at $promptPath. Create it before launching this lead."
        return
    }

    $branch      = _Get-KickoffField -Field "Branch"   -PromptPath $promptPath
    $worktreeRaw = _Get-KickoffField -Field "Worktree" -PromptPath $promptPath
    if (-not $branch -or -not $worktreeRaw) {
        Write-Error "Kickoff $promptPath is missing **Branch:** or **Worktree:** metadata line."
        return
    }
    $worktree = _Resolve-WorktreePath -Raw $worktreeRaw

    if (-not (Test-Path $worktree)) {
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

    # Add to Cursor workspace (idempotent).
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
    if (-not (Test-Path $agentFile)) {
        Write-Warning "No agent file at .claude\agents\$Name.md - falling back to gauntlet-team-lead."
        $agentId = "gauntlet-team-lead"
    }

    # Build claude args. --teammate-mode in-process is constant; --agent picks
    # the lead identity; resume flags (if any) replace the kickoff-prompt
    # positional argument because resuming an existing conversation should
    # not re-inject the kickoff system message.
    $claudeArgs = @("--agent", $agentId, "--teammate-mode", "in-process")
    if ($Fork) { $claudeArgs += "--fork-session" }

    $prompt = Get-Content -Raw $promptPath
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

    $promptPath = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff\$Name.md"
    if (-not (Test-Path $promptPath)) {
        Write-Error "Kickoff prompt not found at $promptPath. Cannot determine which worktree to untrack."
        return
    }
    $worktreeRaw = _Get-KickoffField -Field "Worktree" -PromptPath $promptPath
    if (-not $worktreeRaw) {
        Write-Error "Kickoff $promptPath is missing **Worktree:** metadata line."
        return
    }
    $worktree = _Resolve-WorktreePath -Raw $worktreeRaw

    $workspacePath = _Get-WorkspaceFile
    _Remove-WorkspaceFolder -WorkspacePath $workspacePath -FolderPath $worktree

    _Set-TerminalTitle -Title "AgentForge"

    Write-Host "Stopped tracking $Name. Worktree at $worktree is untouched — run Start-$((Get-Culture).TextInfo.ToTitleCase($Name.ToLower())) to resume." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

function Get-Leads {
    $kickoffDir = Join-Path $AgentForgeRoot ".gauntlet\week2\kickoff"
    if (-not (Test-Path $kickoffDir)) {
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
            Exists   = if ($worktree) { Test-Path $worktree } else { $false }
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
