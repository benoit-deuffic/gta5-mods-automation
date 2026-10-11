"""gtav-ped-mcp: FastMCP server exposing the pipeline's 3 (+1) tools.

Tool names follow spec §3 (generate_raw_3d_asset, compile_gtav_ped,
sync_usermods_manifest), with an added build_delivery_package tool to cover
the decision to deliver via a manual-install zip (docs/architecture-decisions.md
points 2-3) rather than writing directly into a live GTAV_INSTALL_ROOT.

Run with:  uv run --directory . python -m mcp_server.server
Register in Hermes via ~/.hermes/config.yaml `mcp_servers` (hermes.json from
the spec is not a valid Hermes config format, see docs/architecture-decisions.md
point 1).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server import blender_runner, meshy_client, prompt_refiner, validator
from mcp_server.text3d_backend import get_backend
from scripts import package_builder, xml_manifest_updater, ymt_builder

mcp = FastMCP("gtav-ped-mcp")

WORKSPACE_DIR = Path(os.environ.get("PIPELINE_WORKSPACE_DIR", "./workspace")).resolve()
DELIVERY_OUTPUT_DIR = Path(os.environ.get("DELIVERY_OUTPUT_DIR", "./dist")).resolve()
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline.json"
_CONFIG = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def _normalize_target_name(target_name: str) -> str:
    """GTA V/Sollumz convention: asset filenames are always lowercase --
    confirmed for real on bora (2026-10-11): sollumz.export_assets silently
    lowercases its output filename regardless of the Blender object name
    passed in (exporting an armature named "TestPed01" produced
    "testped01.yft.xml" on disk). If target_name isn't normalized at every
    entry point, scripts/codewalker_convert.py's `{model_name}.*` glob looks
    for the wrong case and silently finds nothing (missing files are treated
    as "that component wasn't produced", not an error) -- a silent pipeline
    failure, not a loud one. Normalizing once here, at every tool's first
    line, keeps staging/compiled/export/manifest paths consistent.
    """
    return target_name.lower()


def _staging_dir(target_name: str) -> Path:
    return WORKSPACE_DIR / _normalize_target_name(target_name) / "staging"


def _compiled_dir(target_name: str) -> Path:
    return WORKSPACE_DIR / _normalize_target_name(target_name) / "compiled"


def _manifest_path() -> Path:
    return WORKSPACE_DIR / "ap_m.xml"


@mcp.tool()
def refine_prompt_with_ollama(raw_prompt: str, target_name: str) -> dict:
    """Enrich a short user prompt into a detailed, SFW humanoid description
    using a local Ollama LLM (NOT a 3D generator -- see
    docs/architecture-decisions.md point 7). Falls back to the original
    prompt unchanged (mock=True) if Ollama isn't running/configured.
    Raises if the prompt (or the refined output) contains blocked NSFW terms.
    """
    result = prompt_refiner.refine_prompt(raw_prompt)
    return {
        "target_name": _normalize_target_name(target_name),
        "original_prompt": result.original_prompt,
        "refined_prompt": result.refined_prompt,
        "mock": result.mock,
        "model": result.model,
    }


@mcp.tool()
def generate_raw_3d_asset(prompt: str, target_name: str, art_style: str = "realistic") -> dict:
    """Submit a prompt to the configured text-3D backend (Meshy by default,
    or a local backend via TEXT3D_BACKEND=local), poll until done, download
    FBX + textures to a staging dir.

    `art_style` is accepted for spec compatibility but ignored (deprecated on
    Meshy 6+, see docs/analyse-prerequis.md §2.5). Runs in mock mode
    automatically when no backend credentials/tooling are available.
    """
    target_name = _normalize_target_name(target_name)
    staging_dir = _staging_dir(target_name)
    backend_name = os.environ.get("TEXT3D_BACKEND", "meshy").lower()
    if backend_name == "meshy":
        backend_kwargs = {
            "base_url": _CONFIG.get("meshy", {}).get("api_base_url", "https://api.meshy.ai"),
            "poll_interval_s": _CONFIG.get("meshy", {}).get("poll_interval_seconds", 5),
            "poll_timeout_s": _CONFIG.get("meshy", {}).get("poll_timeout_seconds", 900),
        }
    elif backend_name == "tripo":
        backend_kwargs = {
            "base_url": _CONFIG.get("tripo", {}).get("api_base_url", "https://openapi.tripo3d.ai/v3"),
            "model_version": _CONFIG.get("tripo", {}).get("model_version", "v3.1-20260211"),
            "poll_interval_s": _CONFIG.get("tripo", {}).get("poll_interval_seconds", 3),
            "poll_timeout_s": _CONFIG.get("tripo", {}).get("poll_timeout_seconds", 300),
        }
    else:
        backend_kwargs = {}
    backend = get_backend(backend_name, **backend_kwargs)
    asset = asyncio.run(backend.generate_humanoid(
        prompt=prompt, target_name=target_name, staging_dir=staging_dir,
        pose_mode=_CONFIG.get("meshy", {}).get("pose_mode", "a-pose"),
    ))

    fbx_report = validator.validate_fbx_file(
        asset.fbx_path, min_bytes=_CONFIG.get("validation", {}).get("min_fbx_bytes", 20000))
    tex_reports = [
        validator.validate_texture_file(
            p, min_bytes=_CONFIG.get("validation", {}).get("min_texture_bytes", 5000),
            require_pow2=_CONFIG.get("validation", {}).get("require_power_of_two_textures", True),
        )
        for p in asset.texture_paths
    ]

    return {
        "target_name": target_name,
        "fbx_path": str(asset.fbx_path),
        "texture_paths": [str(p) for p in asset.texture_paths],
        "mock": asset.mock,
        "task_id": asset.task_id,
        "validation": {
            "fbx": {"ok": fbx_report.ok, "errors": fbx_report.errors, "warnings": fbx_report.warnings},
            "textures": [{"ok": r.ok, "errors": r.errors, "warnings": r.warnings} for r in tex_reports],
        },
    }


@mcp.tool()
def compile_gtav_ped(target_name: str, fbx_path: str, gender: str = "female") -> dict:
    """Run headless Blender to rig the FBX onto the GTA ped skeleton and export Sollumz archives.

    Falls back to a clearly-marked mock when Blender/Sollumz/the base-ped
    template aren't installed on this machine (see docs/architecture-decisions.md
    point 6 and templates/README.md).
    """
    target_name = _normalize_target_name(target_name)
    blender_cfg = _CONFIG.get("blender", {})
    template_key = "template_female_path" if gender == "female" else "template_male_path"
    template_path = Path(blender_cfg.get(template_key, f"templates/template_{gender}.blend"))
    compiled_dir = _compiled_dir(target_name)

    result = blender_runner.compile_ped(
        template_path=template_path,
        source_fbx=Path(fbx_path),
        output_dir=compiled_dir,
        model_name=target_name,
        timeout_s=blender_cfg.get("timeout_seconds", 300),
    )

    artifact_report = validator.validate_compiled_artifacts(compiled_dir, target_name)

    return {
        "target_name": target_name,
        "status": result.status,
        "mock": result.mock,
        "output_dir": str(result.output_dir),
        "produced_files": [str(p) for p in result.produced_files],
        "log_tail": result.log[-2000:],
        "validation": {
            "ok": artifact_report.ok,
            "errors": artifact_report.errors,
            "warnings": artifact_report.warnings,
        },
    }


@mcp.tool()
def sync_usermods_manifest(target_name: str, ped_type: str = "CIVFEMALE", gender: str = "female",
                           is_streamed_gfx: bool = False) -> dict:
    """Generate the .ymt, update ap_m.xml, and report manifest registration status.

    Does not write into a live GTAV_INSTALL_ROOT (build host is Linux, game is
    on a separate Windows PC, see docs/architecture-decisions.md point 3);
    produces the manifest/ymt in the workspace for build_delivery_package to
    bundle into the install zip.
    """
    target_name = _normalize_target_name(target_name)
    compiled_dir = _compiled_dir(target_name)
    manifest_path = _manifest_path()

    ymt_path = compiled_dir / f"{target_name}.ymt.xml"
    ymt_builder.write_ymt_xml(ymt_path, target_name)

    xml_manifest_updater.add_ped_to_manifest(
        manifest_path, target_name, gender=gender, is_streamed_gfx=is_streamed_gfx, ped_type=ped_type)

    manifest_report = validator.validate_manifest_xml(manifest_path, target_name)

    return {
        "target_name": target_name,
        "manifest_path": str(manifest_path),
        "ymt_path": str(ymt_path),
        "registered_peds": xml_manifest_updater.list_registered_peds(manifest_path),
        "validation": {
            "ok": manifest_report.ok,
            "errors": manifest_report.errors,
            "warnings": manifest_report.warnings,
        },
    }


@mcp.tool()
def build_delivery_package(target_name: str) -> dict:
    """Package compiled artifacts + manifest into a manual-install zip
    (AddonPeds/PedSelector layout + standalone dlcpack layout), per
    docs/architecture-decisions.md points 2-3.
    """
    target_name = _normalize_target_name(target_name)
    compiled_dir = _compiled_dir(target_name)
    manifest_path = _manifest_path()
    zip_path = package_builder.build_delivery_zip(
        output_dir=DELIVERY_OUTPUT_DIR,
        model_name=target_name,
        compiled_dir=compiled_dir,
        manifest_path=manifest_path,
    )
    return {
        "target_name": target_name,
        "zip_path": str(zip_path),
        "zip_size_bytes": zip_path.stat().st_size,
        "install_instructions": (
            "Extraire le zip, puis suivre README_INSTALL.txt dans "
            "addonpeds_pedselector/ et dlcpack_standalone/<name>/."
        ),
    }


if __name__ == "__main__":
    mcp.run()
