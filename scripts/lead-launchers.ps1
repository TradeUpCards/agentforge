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
# .gauntlet junction (cross-worktree coordination)
# ---------------------------------------------------------------------------
# .gauntlet/ is gitignored (cohort-property PRDs, personal stories, handoffs,
# kickoff prompts, audits). Because git worktrees only inherit *tracked*
# content, a fresh worktree has no .gauntlet/ — but cross-lead coordination
# files (handoffs, in-flight, kickoff) live there and must be visible from
# every worktree. Solution: a directory junction from
# <worktree>\.gauntlet → <main checkout>\.gauntlet. Single source of truth,
# zero exposure to public mirrors, no manual sync.
#
# Idempotent: detects existing junction via a sentinel file and is safe to
# re-run on every Start-Lead invocation.
#
# WARNING: before `git worktree remove <worktree>`, remove the junction
# first to avoid any risk of recursing into the canonical .gauntlet:
#   cmd /c rmdir <worktree>\.gauntlet
# (non-recursive rmdir on a junction removes only the junction, not the
# target — but `git worktree remove` may still recurse, so unjunction first.)

function _Ensure-GauntletJunction {
    param([Parameter(Mandatory=$true)][string]$WorktreePath)

    $target = Join-Path $WorktreePath ".gauntlet"
    $source = Join-Path $AgentForgeRoot ".gauntlet"

    # Sentinel-file check: if a known coordination file is readable through
    # $target, the junction is already in place.
    $sentinel = Join-Path $target "week2\in-flight.md"
    if (Test-Path $sentinel) { return }

    # Empty .gauntlet dir? Remove so junction can take its place.
    if (Test-Path $target) {
        $isEmpty = -not (Get-ChildItem -Path $target -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($isEmpty) {
            Remove-Item $target -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning "Cannot create .gauntlet junction at ${target} - path exists with content."
            Write-Warning "Resolve manually: ensure ${target} is empty or a junction to ${source}."
            return
        }
    }

    # Create directory junction (no admin needed). cmd /c is the simplest path.
    & cmd /c mklink /J "`"$target`"" "`"$source`"" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to junction ${target} -> ${source} via mklink /J (exit $LASTEXITCODE)"
        return
    }
    Write-Host "Junctioned ${target} -> ${source}" -ForegroundColor Cyan
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
    param([Parameter(Mandatory=$true)][string]$Name)

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

    # Add to Cursor workspace (idempotent).
    $workspacePath = _Get-WorkspaceFile
    _Add-WorkspaceFolder -WorkspacePath $workspacePath -FolderPath $worktree

    # Title the terminal tab so you can see which lead is running here.
    $titleCase = (Get-Culture).TextInfo.ToTitleCase($Name.ToLower())
    _Set-TerminalTitle -Title $titleCase

    $prompt = Get-Content -Raw $promptPath
    Push-Location $worktree
    try {
        Write-Host "Launching Claude as $titleCase in $worktree (branch: $branch)..." -ForegroundColor Green
        claude --agent gauntlet-team-lead --teammate-mode in-process $prompt
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

function Start-Aria { Start-Lead -Name "aria" }
function Start-Bram { Start-Lead -Name "bram" }
function Start-Cleo { Start-Lead -Name "cleo" }

function Stop-Aria { Stop-Lead -Name "aria" }
function Stop-Bram { Stop-Lead -Name "bram" }
function Stop-Cleo { Stop-Lead -Name "cleo" }
