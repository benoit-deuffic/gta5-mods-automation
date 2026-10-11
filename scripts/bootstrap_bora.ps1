# Bootstrap script for "bora" — the dedicated Windows workstation
# (i5-14400 / RTX 5070 12GB) that will run Blender+Sollumz natively and
# host GTA V for in-game testing (docs/architecture-decisions.md points
# 4, 8, 9). Idempotent: safe to re-run.
#
# Ollama (local LLM for prompt enrichment, see point 7) is installed INSIDE
# WSL via scripts/bootstrap_ollama.sh, not natively on Windows -- that's
# where the pipeline's Python env and GPU passthrough actually live on this
# host. Confirmed on bora: nvidia-smi inside WSL2 reports the RTX 5070 with
# 12227 MiB VRAM (hw_detect.py tier "gpu-large" -> qwen2.5:14b-instruct +
# llava:13b).
#
# Usage (run as Administrator in PowerShell on bora):
#   irm https://raw.githubusercontent.com/benoit-deuffic/gta5-mods-automation/main/scripts/bootstrap_bora.ps1 | iex
# or locally:
#   .\bootstrap_bora.ps1 [-RepoUrl <url>] [-ProjectDir <path>] `
#     [-WslDistro <name>] [-WslProjectDir <path>] [-SkipGame] [-SkipOllama]

param(
    [string]$RepoUrl = "https://github.com/benoit-deuffic/gta5-mods-automation.git",
    [string]$ProjectDir = "$env:USERPROFILE\gta5-mods-automation",
    [string]$WslDistro = "",
    [string]$WslProjectDir = "/home/benoit/projects/gta5-mods-automation",
    [switch]$SkipGame,
    [switch]$SkipOllama
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

Write-Step "Installing Ollama + ped-generation prompt-refinement models (inside WSL)"
Write-Host @"
Ollama is installed INSIDE WSL, not natively on Windows: this is where the
pipeline's Python env, GPU access (nvidia-smi passthrough confirmed working
in WSL2 on this host), and scripts/prompt_refiner.py all actually run (see
docs/architecture-decisions.md point 7 -- Ollama enriches the text prompt
before 3D generation, it does NOT generate 3D geometry itself). Delegating to
the already-tested scripts/bootstrap_ollama.sh instead of reimplementing
hardware detection in PowerShell.
"@ -ForegroundColor Yellow

if ($SkipOllama) {
    Write-Host "Skipping Ollama setup (-SkipOllama)."
} elseif (-not (Test-CommandExists "wsl")) {
    Write-Warning "WSL not found; cannot install Ollama inside it. Install WSL2 first, or re-run with -SkipOllama."
} else {
    $distroArg = @()
    if ($WslDistro -ne "") { $distroArg = @("-d", $WslDistro) }

    $wslCheck = wsl @distroArg -- test -d $WslProjectDir
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Project not found at $WslProjectDir inside WSL. Clone it there first (git clone $RepoUrl $WslProjectDir), or pass -WslProjectDir <path>. Skipping Ollama setup."
    } else {
        Write-Host "Running scripts/bootstrap_ollama.sh inside WSL (auto-detects this host's RTX 5070 -> gpu-large tier: qwen2.5:14b-instruct + llava:13b)..."
        wsl @distroArg -- bash -lc "cd '$WslProjectDir' && bash scripts/bootstrap_ollama.sh"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "bootstrap_ollama.sh exited non-zero inside WSL (exit $LASTEXITCODE). Check the output above."
        } else {
            Write-Host "Ollama ready inside WSL. config/ollama.json written there; mcp_server/prompt_refiner.py picks it up automatically." -ForegroundColor Green
        }
    }
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
