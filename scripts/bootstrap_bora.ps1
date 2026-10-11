# Bootstrap script for "bora" — the dedicated Windows workstation
# (i5-14400 / RTX 5070 12GB) that will run Blender+Sollumz natively and
# host GTA V for in-game testing (docs/architecture-decisions.md points
# 4, 8, 9). NOT executed/validated from this Linux sandbox — review before
# running on bora. Idempotent: safe to re-run.
#
# Usage (run as Administrator in PowerShell on bora):
#   irm https://raw.githubusercontent.com/benoit-deuffic/gta5-mods-automation/main/scripts/bootstrap_bora.ps1 | iex
# or locally:
#   .\bootstrap_bora.ps1 [-RepoUrl <url>] [-ProjectDir <path>] [-SkipGame]

param(
    [string]$RepoUrl = "https://github.com/benoit-deuffic/gta5-mods-automation.git",
    [string]$ProjectDir = "$env:USERPROFILE\gta5-mods-automation",
    [switch]$SkipGame
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "== $msg ==" -ForegroundColor Cyan
}

function Test-CommandExists($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Step "Hardware check"
try {
    $gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
    if ($gpu) {
        $vramGB = [math]::Round($gpu.AdapterRAM / 1GB, 1)
        Write-Host "GPU: $($gpu.Name) (~$vramGB GB reported by WMI, often inaccurate for VRAM > 4GB -- check nvidia-smi once drivers are installed)"
    } else {
        Write-Warning "No NVIDIA GPU detected via WMI. Expected the RTX 5070 here."
    }
} catch {
    Write-Warning "Could not query GPU info: $_"
}
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$freeDiskGB = [math]::Round((Get-PSDrive -Name ($ProjectDir.Substring(0,1))).Free / 1GB, 1)
Write-Host "RAM: $ramGB GB | Free disk on target drive: $freeDiskGB GB"
if ($freeDiskGB -lt 20) {
    Write-Warning "Less than 20GB free; Blender + Sollumz + .NET SDK + GTA V assets need headroom."
}

Write-Step "Checking winget"
if (-not (Test-CommandExists "winget")) {
    throw "winget not found. Install 'App Installer' from the Microsoft Store first, then re-run."
}

Write-Step "Installing Blender (if absent)"
if (-not (Test-CommandExists "blender")) {
    winget install --id BlenderFoundation.Blender -e --accept-source-agreements --accept-package-agreements
    Write-Host "Blender installed. You may need to open a NEW PowerShell window for PATH to update."
} else {
    Write-Host "Blender already on PATH: $(Get-Command blender | Select-Object -ExpandProperty Source)"
}

Write-Step "Installing .NET SDK (for CodeWalker.Core CLI, if absent)"
if (-not (Test-CommandExists "dotnet")) {
    winget install --id Microsoft.DotNet.SDK.8 -e --accept-source-agreements --accept-package-agreements
} else {
    Write-Host ".NET SDK already present: $(dotnet --version)"
}

Write-Step "Installing Git (if absent)"
if (-not (Test-CommandExists "git")) {
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
} else {
    Write-Host "Git already present: $(git --version)"
}

Write-Step "Installing Python 3.11 + uv (if absent)"
if (-not (Test-CommandExists "uv")) {
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
} else {
    Write-Host "uv already present: $(uv --version)"
}

Write-Step "Checking Tailscale (should already be joined per docs/architecture-decisions.md)"
if (Test-CommandExists "tailscale") {
    tailscale status
} else {
    Write-Warning "Tailscale not found. Install from https://tailscale.com/download/windows if this host needs remote access."
}

Write-Step "Cloning/updating the project repo"
if (Test-Path $ProjectDir) {
    Write-Host "Repo already present at $ProjectDir, pulling latest..."
    git -C $ProjectDir pull --ff-only
} else {
    git clone $RepoUrl $ProjectDir
}

Write-Step "Creating Python venv and installing pipeline deps"
Push-Location $ProjectDir
uv venv --python 3.11 .venv
uv pip install --python .venv -e . --group dev
Pop-Location

Write-Step "Sollumz extension (MANUAL STEP — cannot be scripted reliably)"
Write-Host @"
Sollumz is a Blender EXTENSION, not installable via winget/pip:
  1. Open Blender -> Edit > Preferences > Get Extensions.
  2. Search 'Sollumz', install version >= 2.9.0 (pin a release tag, not main).
  3. Enable it, then in Blender's Python console run:
       import subprocess, sys
       subprocess.run([sys.executable, '-m', 'pip', 'install', 'szio==1.4.0.dev2'])
     (szio is Sollumz's required dependency, not bundled.)
  See docs/analyse-prerequis.md and the gtav-ped-modding skill's
  references/toolchain-facts.md for details.
"@ -ForegroundColor Yellow

if (-not $SkipGame) {
    Write-Step "GTA V / mod tooling (MANUAL STEP)"
    Write-Host @"
Not scripted (needs your own game copy + manual choices):
  1. Locate your GTA V install (Documents/Rockstar Games/GTA V for UserMods).
  2. Install CodeWalker (https://github.com/dexyfex/CodeWalker/releases) to
     extract the base ped templates (see templates/README.md in this repo).
  3. For in-game testing (spec acceptance criteria 8.4): ScriptHookV,
     ScriptHookVDotNet3, AddonPeds/PedSelector, Menyoo.
"@ -ForegroundColor Yellow
}

Write-Step "Done"
Write-Host "Next: run '$ProjectDir\.venv\Scripts\python.exe -m pytest -q' to confirm the pipeline still passes on this host,"
Write-Host "then follow templates/README.md to produce the real template_female/template_male .blend files."
