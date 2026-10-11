#!/usr/bin/env bash
# Prepares a Python env for LOCAL text-to-3D generation (TripoSR or
# Hunyuan3D-2) on a CUDA-capable GPU workstation. NOT meant to run on this
# dev sandbox (no GPU, ~8GB disk -- PyTorch + CUDA + model weights need
# 10-20GB+). Written here so it travels with the project when duplicated to
# the dedicated Windows/Linux workstation (i5-14400 / RTX 5070 12GB), per
# docs/architecture-decisions.md point 8.
#
# This script is NOT executed/validated in this sandbox. Review and adapt
# CUDA/torch versions to the target GPU driver before running it there.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv-local3d"
BACKEND="${1:-triposr}"   # triposr | hunyuan3d-2

echo "== Hardware check =="
python3 "$SCRIPT_DIR/hw_detect.py"
echo "This script requires an NVIDIA GPU with >= 8GB VRAM (gpu-small/gpu-large tier)."
echo "If hw_detect.py above does not show has_nvidia_gpu=True, STOP: it will not run."

echo "== Creating dedicated venv (kept separate from the main pipeline venv) =="
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip

case "$BACKEND" in
  triposr)
    echo "== Installing TripoSR deps =="
    # https://github.com/VAST-AI-Research/TripoSR
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
    pip install -r <(cat <<'REQS'
omegaconf==2.3.0
einops==0.7.0
transformers==4.38.0
trimesh==4.1.0
rembg==2.0.56
huggingface-hub
imageio
onnxruntime
xatlas
moderngl
REQS
    )
    echo "Clone https://github.com/VAST-AI-Research/TripoSR into $PROJECT_ROOT/third_party/TripoSR"
    echo "and point scripts/local_3d_generate.py --backend triposr at its run.py."
    ;;
  hunyuan3d-2)
    echo "== Installing Hunyuan3D-2 deps =="
    # https://github.com/Tencent/Hunyuan3D-2
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
    pip install -r <(cat <<'REQS'
diffusers
transformers
accelerate
trimesh
pymeshlab
huggingface-hub
REQS
    )
    echo "Clone https://github.com/Tencent/Hunyuan3D-2 into $PROJECT_ROOT/third_party/Hunyuan3D-2"
    echo "and point scripts/local_3d_generate.py --backend hunyuan3d-2 at its inference script."
    ;;
  *)
    echo "Unknown backend: $BACKEND (expected triposr | hunyuan3d-2)" >&2
    exit 1
    ;;
esac

echo "== Done. NOTE: scripts/local_3d_generate.py (the CLI called by =="
echo "mcp_server/text3d_backend.py LocalText3DBackend) still needs to be written"
echo "against whichever backend's actual Python API once it's cloned and"
echo "running on the GPU host -- this script only prepares the environment."
