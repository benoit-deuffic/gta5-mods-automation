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
