#!/usr/bin/env bash
# Bootstrap Ollama + pull models sized to the host running this script.
#
# Purpose in this pipeline: Ollama provides a LOCAL LLM used to enrich/refine
# the user's raw text prompt into a more detailed humanoid description before
# it's sent to the 3D generation backend (Meshy or a local backend). It does
# NOT generate 3D geometry itself -- Ollama only serves text/vision LLMs.
# See docs/architecture-decisions.md point 7.
#
# This same script is meant to run unmodified on:
#   - this dev sandbox (no GPU, ~1.9GB RAM)  -> picks tiny CPU models
#   - the future dedicated Windows/Linux workstation (RTX 5070 12GB VRAM)
#     -> picks a 7-14B instruct model + a vision model for render QA
# Hardware detection lives in scripts/hw_detect.py (pure Python, testable).
#
# Usage:
#   scripts/bootstrap_ollama.sh [--dry-run] [--text-model NAME] [--vision-model NAME] [--skip-vision]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
[ -x "$PROJECT_ROOT/.venv/bin/python" ] && PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

DRY_RUN=0
OVERRIDE_TEXT_MODEL=""
OVERRIDE_VISION_MODEL=""
SKIP_VISION=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --text-model) OVERRIDE_TEXT_MODEL="$2"; shift 2 ;;
    --vision-model) OVERRIDE_VISION_MODEL="$2"; shift 2 ;;
    --skip-vision) SKIP_VISION=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

echo "== Hardware detection =="
HW_JSON="$("$PYTHON_BIN" "$SCRIPT_DIR/hw_detect.py" --json)"
echo "$HW_JSON"

TEXT_MODEL="${OVERRIDE_TEXT_MODEL:-$(echo "$HW_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["ollama_models"]["text"])')}"
VISION_MODEL="${OVERRIDE_VISION_MODEL:-$(echo "$HW_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["ollama_models"]["vision"] or "")')}"
FREE_DISK_MB="$(echo "$HW_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["profile"]["free_disk_mb"])')"

echo "== Plan =="
echo "text model   : $TEXT_MODEL"
if [ "$SKIP_VISION" -eq 1 ] || [ -z "$VISION_MODEL" ]; then
  echo "vision model : (skipped)"
  VISION_MODEL=""
else
  echo "vision model : $VISION_MODEL"
fi
echo "free disk    : ${FREE_DISK_MB} MB"

if [ "$FREE_DISK_MB" -lt 3000 ]; then
  echo "WARNING: less than 3GB free disk; model pulls may fail or fill the disk." >&2
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] would install Ollama (if absent) and pull: $TEXT_MODEL ${VISION_MODEL}"
  exit 0
fi

echo "== Installing Ollama (if absent) =="
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "ollama already installed: $(command -v ollama)"
fi

echo "== Ensuring ollama server is running =="
if ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  nohup ollama serve >"$PROJECT_ROOT/workspace_ollama_serve.log" 2>&1 &
  for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi
curl -fsS http://127.0.0.1:11434/api/version

echo "== Pulling text model: $TEXT_MODEL =="
ollama pull "$TEXT_MODEL"

if [ -n "$VISION_MODEL" ]; then
  echo "== Pulling vision model: $VISION_MODEL =="
  ollama pull "$VISION_MODEL"
fi

mkdir -p "$PROJECT_ROOT/config"
cat > "$PROJECT_ROOT/config/ollama.json" <<EOF
{
  "endpoint": "http://127.0.0.1:11434",
  "text_model": "$TEXT_MODEL",
  "vision_model": "$([ -n "$VISION_MODEL" ] && echo "$VISION_MODEL" || echo null)",
  "hardware_profile": $HW_JSON
}
EOF

echo "== Done. Config written to config/ollama.json =="
echo "Test with: curl http://127.0.0.1:11434/api/generate -d '{\"model\":\"$TEXT_MODEL\",\"prompt\":\"hi\",\"stream\":false}'"
