"""Pluggable text-to-3D backend interface, so the orchestration layer doesn't
hard-code any single cloud provider. See docs/architecture-decisions.md
point 5 (kept portable for a future local backend), point 9 (Tripo3D),
point 17 (Meshy dropped from the project, "je ne l'utiliserai pas") and
point 7 (Ollama handles prompt refinement only; this module is the actual,
separate 3D-geometry generation step).

Implementations:
- TripoBackend      : wraps mcp_server.tripo_client.TripoClient (cloud API).
  Default backend when TEXT3D_BACKEND is unset.
- LocalText3DBackend: calls scripts/local_3d_generate.py, a thin CLI wrapper
  meant to run TripoSR or Hunyuan3D-2 on a CUDA GPU (>=8GB VRAM). This dev
  sandbox has no GPU, so LocalText3DBackend always falls back to the same
  fixture-based mock used by the cloud backends (see
  mcp_server.text3d_types.GeneratedAsset / FIXTURES_DIR) rather than
  fabricating fake geometry -- identical honesty policy as the rest of the
  pipeline (blender_runner, codewalker_convert).

Selected via TEXT3D_BACKEND env var ("tripo" default | "local"), or passed
explicitly.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from mcp_server.text3d_types import FIXTURES_DIR as _MOCK_FIXTURES_DIR
from mcp_server.text3d_types import GeneratedAsset, Text3DBackendError
from mcp_server.tripo_client import TripoClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
LOCAL_3D_SCRIPT = SCRIPTS_DIR / "local_3d_generate.py"


class Text3DBackend(Protocol):
    async def generate_humanoid(self, prompt: str, target_name: str, staging_dir: Path,
                                 pose_mode: str = "a-pose") -> GeneratedAsset:
        ...


class LocalText3DBackend:
    """Local, self-hosted text-to-3D (TripoSR / Hunyuan3D-2), for the future
    GPU workstation (docs/architecture-decisions.md). Requires:
      - scripts/local_3d_generate.py to exist and be runnable (not written in
        this dev sandbox; see scripts/bootstrap_local_3d_env.sh).
      - A CUDA-capable GPU with enough VRAM (the bootstrap script's hw_detect
        tier gpu-small/gpu-large).
    On this sandbox (no GPU, script not implemented) this always mock-falls-back.

    Mock-mode is decided purely by PIPELINE_MOCK + local script availability --
    this backend is self-hosted and has no cloud API key of its own, so unlike
    TripoBackend it never looks at any *_API_KEY env var.
    """

    def __init__(self, python_bin: str | None = None):
        self.python_bin = python_bin or os.environ.get("LOCAL_3D_PYTHON", "python3")

    def is_available(self) -> bool:
        return LOCAL_3D_SCRIPT.exists() and shutil.which(self.python_bin) is not None

    def _mock_forced(self) -> bool | None:
        forced = os.environ.get("PIPELINE_MOCK", "auto").strip().lower()
        if forced in ("1", "true", "yes"):
            return True
        if forced in ("0", "false", "no"):
            return False
        return None  # "auto": decide from is_available() alone

    async def generate_humanoid(self, prompt: str, target_name: str, staging_dir: Path,
                                 pose_mode: str = "a-pose") -> GeneratedAsset:
        staging_dir.mkdir(parents=True, exist_ok=True)

        force_mock = self._mock_forced()
        if force_mock is True or (force_mock is None and not self.is_available()):
            return self._mock(target_name, staging_dir,
                               reason="local 3D backend not implemented/available on this host")

        cmd = [
            self.python_bin, str(LOCAL_3D_SCRIPT),
            "--prompt", prompt, "--target-name", target_name,
            "--output-dir", str(staging_dir), "--pose-mode", pose_mode,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise Text3DBackendError(f"local_3d_generate.py failed: {proc.stderr[-2000:]}")

        fbx_path = staging_dir / f"{target_name}.fbx"
        textures = sorted(staging_dir.glob(f"{target_name}_*.png"))
        if not fbx_path.exists():
            raise Text3DBackendError(f"local_3d_generate.py did not produce {fbx_path}")
        return GeneratedAsset(fbx_path=fbx_path, texture_paths=textures, mock=False, task_id="local")

    def _mock(self, target_name: str, staging_dir: Path, reason: str) -> GeneratedAsset:
        import shutil as _shutil
        fbx_fixture = _MOCK_FIXTURES_DIR / "mock_humanoid.fbx"
        tex_fixture = _MOCK_FIXTURES_DIR / "mock_texture.png"
        if not fbx_fixture.exists() or not tex_fixture.exists():
            raise Text3DBackendError(f"Mock fixtures missing and local backend unavailable ({reason}).")
        fbx_path = staging_dir / f"{target_name}.fbx"
        tex_path = staging_dir / f"{target_name}_base_color_0.png"
        _shutil.copy2(fbx_fixture, fbx_path)
        _shutil.copy2(tex_fixture, tex_path)
        return GeneratedAsset(fbx_path=fbx_path, texture_paths=[tex_path], mock=True, task_id=f"local-mock ({reason})")


class TripoBackend:
    """Adapter so TripoClient satisfies the Text3DBackend protocol.

    The default backend (see get_backend() below): the sole cloud text-to-3D
    provider used by this project since Meshy was removed (docs/
    architecture-decisions.md point 17).

    NOTE: Tripo3D only returns a GLB (mesh + embedded PBR textures, no
    separate texture files) -- see mcp_server/tripo_client.py module
    docstring. compile_gtav_ped's Blender step currently only imports FBX;
    importing the GLB natively (bpy.ops.import_scene.gltf) is not yet wired
    up since there is no Blender on this sandbox to test it against.
    """

    def __init__(self, **kwargs):
        self._client = TripoClient(**kwargs)

    async def generate_humanoid(self, prompt: str, target_name: str, staging_dir: Path,
                                 pose_mode: str = "a-pose") -> GeneratedAsset:
        return await self._client.generate_humanoid(prompt, target_name, staging_dir, pose_mode)


def get_backend(name: str | None = None, **kwargs) -> Text3DBackend:
    name = (name or os.environ.get("TEXT3D_BACKEND", "tripo")).strip().lower()
    if name == "local":
        return LocalText3DBackend(**kwargs)
    if name == "tripo":
        return TripoBackend(**kwargs)
    raise ValueError(f"Unknown TEXT3D_BACKEND={name!r}, expected 'tripo' or 'local'.")
