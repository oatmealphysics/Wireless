<#
.SYNOPSIS
    Sets up Claude Code with cybersecurity skills, plugins, and configuration.
.DESCRIPTION
    Run this from the setup/ directory of the Wireless repo clone.
    It copies config files to %USERPROFILE%\.claude\ and installs marketplace plugins.
    Requires: Claude Code CLI already installed (npm install -g @anthropic-ai/claude-code).
.NOTES
    Run as: powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

$ErrorActionPreference = "Stop"
$ClaudeDir = Join-Path $env:USERPROFILE ".claude"
$SkillsDir = Join-Path $ClaudeDir "skills"
$ConfigSource = Join-Path $PSScriptRoot "claude-config"

Write-Host "`n=== Claude Code Cybersecurity Setup ===" -ForegroundColor Cyan
Write-Host "Source:      $ConfigSource"
Write-Host "Destination: $ClaudeDir`n"

# --- Pre-flight checks ---
if (-not (Get-Command "claude" -ErrorAction SilentlyContinue)) {
    Write-Host "[!] 'claude' not found in PATH." -ForegroundColor Red
    Write-Host "    Install it first:  npm install -g @anthropic-ai/claude-code"
    Write-Host "    Then run this script again."
    exit 1
}

if (-not (Test-Path $ConfigSource)) {
    Write-Host "[!] Config source not found at $ConfigSource" -ForegroundColor Red
    Write-Host "    Run this script from the setup/ directory of the Wireless repo."
    exit 1
}

# --- Create directories ---
Write-Host "[1/5] Creating directories..." -ForegroundColor Yellow
if (-not (Test-Path $ClaudeDir)) { New-Item -ItemType Directory -Path $ClaudeDir | Out-Null }
if (-not (Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir | Out-Null }

# --- Copy CLAUDE.md ---
Write-Host "[2/5] Copying global instructions (CLAUDE.md)..." -ForegroundColor Yellow
$src = Join-Path $ConfigSource "CLAUDE.md"
$dst = Join-Path $ClaudeDir "CLAUDE.md"
if (Test-Path $dst) {
    $backup = "${dst}.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "       Backing up existing CLAUDE.md -> $backup"
    Copy-Item $dst $backup
}
Copy-Item $src $dst -Force
Write-Host "       -> $dst" -ForegroundColor Green

# --- Copy settings.json ---
Write-Host "[3/5] Copying settings.json..." -ForegroundColor Yellow
$src = Join-Path $ConfigSource "settings.json"
$dst = Join-Path $ClaudeDir "settings.json"
if (Test-Path $dst) {
    $backup = "${dst}.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "       Backing up existing settings.json -> $backup"
    Copy-Item $dst $backup
}
Copy-Item $src $dst -Force
Write-Host "       -> $dst" -ForegroundColor Green

# --- Copy statusline.py ---
Write-Host "       Copying statusline.py..."
$src = Join-Path $ConfigSource "statusline.py"
$dst = Join-Path $ClaudeDir "statusline.py"
Copy-Item $src $dst -Force
Write-Host "       -> $dst" -ForegroundColor Green

# --- Copy custom skills ---
Write-Host "[4/5] Copying custom skills..." -ForegroundColor Yellow
$skillFiles = Get-ChildItem (Join-Path $ConfigSource "skills") -Filter "*.md"
foreach ($skill in $skillFiles) {
    $dst = Join-Path $SkillsDir $skill.Name
    Copy-Item $skill.FullName $dst -Force
    Write-Host "       -> $($skill.Name)" -ForegroundColor Green
}

# --- Install marketplace plugins ---
Write-Host "[5/5] Installing marketplace plugins..." -ForegroundColor Yellow
Write-Host "       (Each plugin prompts for confirmation in Claude Code)" -ForegroundColor DarkGray

$plugins = @(
    @{ Name = "Matt Pocock Skills";              Cmd = "mattpocock/skills" },
    @{ Name = "Masriyan Cybersecurity Skills";    Cmd = "Masriyan/Claude-Code-CyberSecurity-Skill" },
    @{ Name = "Anthropic Cybersecurity Skills";   Cmd = "mukul975/Anthropic-Cybersecurity-Skills" },
    @{ Name = "Offensive Claude";                 Cmd = "hypnguyen1209/offensive-claude" },
    @{ Name = "Trail of Bits Skills";             Cmd = "trailofbits/skills" }
)

Write-Host ""
Write-Host "       Marketplace plugins must be installed inside a Claude Code session."
Write-Host "       After this script finishes, open Claude Code and run these commands:`n" -ForegroundColor Cyan

foreach ($p in $plugins) {
    Write-Host "         /plugin marketplace add $($p.Cmd)" -ForegroundColor White
}

Write-Host ""
Write-Host "       For REA (Reverse Engineer Anything), also run:"
Write-Host "         npx skills add morluto/rea" -ForegroundColor White

# --- Verify ---
Write-Host "`n=== Verification ===" -ForegroundColor Cyan
$checks = @(
    @{ File = (Join-Path $ClaudeDir "CLAUDE.md");       Label = "CLAUDE.md" },
    @{ File = (Join-Path $ClaudeDir "settings.json");   Label = "settings.json" },
    @{ File = (Join-Path $ClaudeDir "statusline.py");   Label = "statusline.py" }
)
foreach ($c in $checks) {
    if (Test-Path $c.File) {
        Write-Host "  [OK] $($c.Label)" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $($c.Label)" -ForegroundColor Red
    }
}
$installedSkills = (Get-ChildItem $SkillsDir -Filter "*.md" -ErrorAction SilentlyContinue).Count
Write-Host "  [OK] $installedSkills custom skills installed" -ForegroundColor Green

Write-Host "`n=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Open a terminal and run: claude"
Write-Host "  2. Install the marketplace plugins listed above"
Write-Host "  3. Verify with: /help  (should show all skills)`n"
