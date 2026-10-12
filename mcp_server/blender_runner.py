"""Subprocess wrapper to invoke Blender headless for rigging/export, with a
mock fallback when Blender isn't installed on this machine (it currently
isn't — see docs/architecture-decisions.md point 6).

The mock path does NOT fabricate fake binary RAGE files. It writes small,
clearly-labeled CW-XML placeholder files with the right names/extensions so
the rest of the pipeline (manifest sync, packaging, validator) can be
exercised end-to-end, while every placeholder is tagged mock=True in the
returned result and a [MOCK] marker is written inside the file content.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
RIG_SCRIPT = SCRIPTS_DIR / "blender_rig_export.py"
LAYERED_SCRIPT = SCRIPTS_DIR / "import_layered_ped.py"


@dataclass
class CompileResult:
    status: str  # "compiled" | "mock" | "error"
    output_dir: Path
    produced_files: list[Path] = field(default_factory=list)
    log: str = ""
    mock: bool = False


def _resolve_blender_path() -> str | None:
    configured = os.environ.get("BLENDER_EXECUTABLE_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured
    return shutil.which("blender")


def is_blender_available() -> bool:
    return _resolve_blender_path() is not None


def compile_ped(template_path: Path, source_fbx: Path, output_dir: Path, model_name: str,
                 timeout_s: int = 300) -> CompileResult:
    """Run Blender headless on a source mesh from ANY configured backend.

    `source_fbx` is a historical name kept for API stability; it accepts
    both .fbx (local backend) and .glb/.gltf (Tripo3D) --
    scripts/blender_rig_export.py picks the right importer by extension (see
    its import_source_mesh()).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    blender_path = _resolve_blender_path()

    if not blender_path:
        return _mock_compile(output_dir, model_name,
                              reason="Blender executable not found on PATH/BLENDER_EXECUTABLE_PATH")

    if not template_path.exists():
        return _mock_compile(output_dir, model_name,
                              reason=f"Template not found: {template_path} (see templates/README.md)")

    cmd = [
        blender_path, "--background", "--python", str(RIG_SCRIPT), "--",
        "--template", str(template_path),
        "--source-fbx", str(source_fbx),
        "--output-dir", str(output_dir),
        "--model-name", model_name,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return CompileResult(status="error", output_dir=output_dir, log=str(exc))

    produced = sorted(output_dir.glob(f"{model_name}.*"))
    if proc.returncode != 0:
        return CompileResult(status="error", output_dir=output_dir,
                              produced_files=produced, log=proc.stdout[-4000:] + proc.stderr[-4000:])

    return CompileResult(status="compiled", output_dir=output_dir, produced_files=produced,
                          log=proc.stdout[-4000:], mock=False)


def _mock_compile(output_dir: Path, model_name: str, reason: str) -> CompileResult:
    produced = []
    for suffix in (".ydd.xml", ".yft.xml", ".ytd.xml"):
        path = output_dir / f"{model_name}{suffix}"
        path.write_text(
            f"<!-- [MOCK] placeholder generated without Blender/Sollumz. Reason: {reason}. "
            f"Not a valid RAGE XML, do not ship. -->\n",
            encoding="utf-8",
        )
        produced.append(path)
    return CompileResult(status="mock", output_dir=output_dir, produced_files=produced,
                          log=f"[MOCK] {reason}", mock=True)


@dataclass
class LayeredBuildResult:
    status: str  # "built" | "error"
    output_blend: Path
    preview_png: Path | None = None
    warnings: list[str] = field(default_factory=list)  # the script's [WARN]/[FAIL] lines
    measurements: list[str] = field(default_factory=list)  # the script's [BUST]/[GLUTES] lines
    log: str = ""


def build_layered_ped(template_path: Path, ped_root: Path, ped_name: str, output_blend: Path,
                      bust_size: str | None = None, glutes: str | None = None,
                      glute_height: str | None = None,
                      preview_png: Path | None = None,
                      skeleton_yft: Path | None = None,
                      timeout_s: int = 900) -> LayeredBuildResult:
    """Run scripts/import_layered_ped.py headless: layered component
    resolution + Sollumz import (native skinning via <ped_root>/<ped>.yft.xml,
    else `skeleton_yft`, which must match the template's skeleton)
    + texture fixes + optional bust fit to an EU bra size and glute fit to a
    GLUTE_SHAPES shape / GLUTE_HEIGHTS height (measured on the mesh,
    body_morphology.fit_bust / fit_glutes). No mock fallback -- unlike compile_ped
    there is no meaningful placeholder for a .blend."""
    blender_path = _resolve_blender_path()
    if not blender_path:
        return LayeredBuildResult(status="error", output_blend=output_blend,
                                  log="Blender executable not found on PATH/BLENDER_EXECUTABLE_PATH")
    if not template_path.exists():
        return LayeredBuildResult(status="error", output_blend=output_blend,
                                  log=f"Template not found: {template_path}")

    cmd = [
        blender_path, "--background", "--python-exit-code", "1", "--python", str(LAYERED_SCRIPT), "--",
        "--template", str(template_path),
        "--ped-root", str(ped_root),
        "--ped-name", ped_name,
        "--output-blend", str(output_blend),
    ]
    if bust_size:
        cmd += ["--bust-size", bust_size]
    if glutes:
        cmd += ["--glutes", glutes]
    if glute_height:
        cmd += ["--glute-height", glute_height]
    if skeleton_yft:
        cmd += ["--skeleton-yft", str(skeleton_yft)]
    if preview_png:
        cmd += ["--preview-png", str(preview_png)]
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    output_blend.unlink(missing_ok=True)  # a previous run's file must not pass for this one's
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return LayeredBuildResult(status="error", output_blend=output_blend, log=str(exc))

    warnings = [line for line in proc.stdout.splitlines() if line.startswith(("[WARN]", "[FAIL]"))]
    measurements = [line for line in proc.stdout.splitlines() if line.startswith(("[BUST]", "[GLUTES]"))]
    # --python-exit-code makes a Python exception in the script a nonzero exit
    # (Blender exits 0 by default even then).
    ok = proc.returncode == 0 and output_blend.exists()
    return LayeredBuildResult(status="built" if ok else "error", output_blend=output_blend,
                              preview_png=preview_png if preview_png and preview_png.exists() else None,
                              warnings=warnings, measurements=measurements,
                              log=proc.stdout[-4000:] + proc.stderr[-2000:])
