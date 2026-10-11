"""Subprocess wrapper to invoke the headless AI texture reskin pipeline
(scripts/ai_texture_reskin.py), with a mock fallback when Blender or the
sd-local venv's Python aren't installed on this machine -- same pattern as
mcp_server/blender_runner.py's compile_ped().

The mock path does NOT fabricate fake texture/image content. It writes a
single small placeholder PNG tagged [MOCK] so the rest of the pipeline can
be exercised end-to-end, with mock=True always surfaced to the caller.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
RESKIN_SCRIPT = SCRIPTS_DIR / "ai_texture_reskin.py"

DEFAULT_SD_PYTHON = os.path.expanduser("~/tools/sd-local/venv/bin/python")


@dataclass
class ReskinResult:
    status: str  # "generated" | "mock" | "error"
    output_dir: Path
    produced_files: list[Path] = field(default_factory=list)
    log: str = ""
    mock: bool = False


def _resolve_blender_path() -> str | None:
    configured = os.environ.get("BLENDER_EXECUTABLE_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured
    return shutil.which("blender")


def _resolve_sd_python() -> str | None:
    configured = os.environ.get("SD_LOCAL_PYTHON", "").strip()
    candidate = configured or DEFAULT_SD_PYTHON
    return candidate if Path(candidate).exists() else None


def is_pipeline_available() -> bool:
    return _resolve_blender_path() is not None and _resolve_sd_python() is not None


def generate_reskin(template_path: Path, output_dir: Path, model_name: str, prompt: str,
                     skin_prompt: str | None = None,
                     num_cameras: int = 6, texture_size: int = 512,
                     controlnet_conditioning_scale: float = 0.8,
                     steps: int = 25, timeout_s: int = 900) -> ReskinResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    blender_path = _resolve_blender_path()
    sd_python = _resolve_sd_python()

    if not blender_path:
        return _mock_reskin(output_dir, model_name, reason="Blender executable not found on PATH/BLENDER_EXECUTABLE_PATH")
    if not sd_python:
        return _mock_reskin(output_dir, model_name, reason="sd-local venv Python not found (SD_LOCAL_PYTHON / ~/tools/sd-local/venv/bin/python)")
    if not template_path.exists():
        return _mock_reskin(output_dir, model_name, reason=f"Template not found: {template_path}")

    cmd = [
        blender_path, "--background", "--python", str(RESKIN_SCRIPT), "--",
        "--template", str(template_path),
        "--output-dir", str(output_dir),
        "--model-name", model_name,
        "--prompt", prompt,
        "--num-cameras", str(num_cameras),
        "--texture-size", str(texture_size),
        "--controlnet-conditioning-scale", str(controlnet_conditioning_scale),
        "--steps", str(steps),
        "--sd-python", sd_python,
    ]
    if skin_prompt:
        cmd += ["--skin-prompt", skin_prompt]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return ReskinResult(status="error", output_dir=output_dir, log=str(exc))

    # Pattern must tolerate the per-component naming scheme
    # ("<model>_<component>_reskin.png", e.g. "testped01_uppr_reskin.png")
    # introduced when ai_texture_reskin.py started reskinning every body
    # component (uppr/lowr/hair/...) instead of a single mesh -- the old
    # "<model>_reskin*" prefix-only glob stopped matching those.
    produced = sorted(output_dir.glob(f"{model_name}_*reskin*"))
    if proc.returncode != 0:
        return ReskinResult(status="error", output_dir=output_dir, produced_files=produced,
                             log=proc.stdout[-4000:] + proc.stderr[-4000:])

    return ReskinResult(status="generated", output_dir=output_dir, produced_files=produced,
                         log=proc.stdout[-4000:], mock=False)


def _mock_reskin(output_dir: Path, model_name: str, reason: str) -> ReskinResult:
    path = output_dir / f"{model_name}_reskin.png.txt"
    path.write_text(
        f"[MOCK] placeholder generated without Blender/sd-local. Reason: {reason}. "
        "Not a real texture, do not ship.\n", encoding="utf-8",
    )
    return ReskinResult(status="mock", output_dir=output_dir, produced_files=[path], log=f"[MOCK] {reason}", mock=True)
