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

from mcp_server import ai_reskin_runner, blender_runner, meshy_client, prompt_refiner, validator
from mcp_server.text3d_backend import get_backend
from scripts import codewalker_convert, package_builder, xml_manifest_updater, ymt_builder

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

    # Blender/Sollumz only ever produces CodeWalker-XML (CWXML) on Linux --
    # native binary export needs PyMateria (Windows-only, see
    # docs/architecture-decisions.md point 12). Without this step the
    # delivery zip would only ever contain .ydd.xml/.yft.xml/.ytd.xml, never
    # the real .ydd/.yft/.ytd binaries the game actually loads -- a silent
    # wiring gap: nothing upstream called scripts/codewalker_convert.py, it
    # was only ever exercised directly by tests/test_codewalker_cli_integration.py.
    conversion = None
    if result.status == "compiled":
        conv_result = codewalker_convert.convert_xml_to_binary(
            xml_dir=compiled_dir, output_dir=compiled_dir, model_name=target_name,
            extensions=(".ydd", ".yft", ".ytd"),
        )
        conversion = {
            "status": conv_result.status,
            "produced_files": conv_result.produced_files,
            "message": conv_result.message,
        }
        if conv_result.produced_files:
            result.produced_files = sorted(set(result.produced_files) | {Path(p) for p in conv_result.produced_files})

    artifact_report = validator.validate_compiled_artifacts(compiled_dir, target_name)

    return {
        "target_name": target_name,
        "status": result.status,
        "mock": result.mock,
        "output_dir": str(result.output_dir),
        "produced_files": [str(p) for p in result.produced_files],
        "log_tail": result.log[-2000:],
        "binary_conversion": conversion,
        "validation": {
            "ok": artifact_report.ok,
            "errors": artifact_report.errors,
            "warnings": artifact_report.warnings,
        },
    }


@mcp.tool()
def generate_ai_texture_reskin(target_name: str, prompt: str, skin_prompt: str | None = None,
                                num_cameras: int = 6,
                                texture_size: int = 512, controlnet_conditioning_scale: float = 0.8,
                                steps: int = 25, gender: str = "female") -> dict:
    """Headless, multi-camera, UV-aware AI texture reskin for a GTA V ped
    (scripts/ai_texture_reskin.py): renders N true per-pixel depth-map
    camera views of the ped template mesh, synthesizes a new look per view
    with a local SDXL + ControlNet-depth pass (scripts/sd_view_synthesis.py,
    run in the sd-local venv, text-to-image driven by the prompt and
    structurally constrained by the depth map), and bakes the results back
    onto the mesh's real diffuse UV layout.

    `prompt` is the main/clothing look, applied to every camera region NOT
    classified as skin-dominant. `skin_prompt` (optional) is used instead for
    cameras whose assigned faces are dominantly weighted to skin vertex
    groups (head/neck/hands/fingers) -- defaults to a neutral "natural skin
    tone" prompt when not given, so skin regions never silently inherit the
    clothing material anymore (docs/architecture-decisions.md point 15).

    This is a reskin/visual-preview step, NOT a replacement for
    compile_gtav_ped -- it does not produce a new rigged mesh, only a
    candidate texture (<target_name>_reskin.png in the output dir). Round
    trip to a real binary .ytd is a separate manual step via
    scripts/dds_pack_and_validate.py once a texture is approved (not run
    automatically here -- see docs/architecture-decisions.md for why, incl.
    a known codewalker-cli `inspect` round-trip bug reproduced even on an
    unmodified base asset).

    Falls back to a clearly-marked mock when Blender or the sd-local venv
    aren't installed on this machine (see mcp_server/ai_reskin_runner.py).

    Applies the same NSFW/content guardrail as refine_prompt_with_ollama
    (prompt_refiner's blocked-keyword list) before running anything, since
    this tool also accepts a raw user prompt feeding an external generator
    -- the check runs on BOTH prompt and skin_prompt when given.
    """
    target_name = _normalize_target_name(target_name)
    prompt_refiner._check_blocked(prompt)
    if skin_prompt:
        prompt_refiner._check_blocked(skin_prompt)

    blender_cfg = _CONFIG.get("blender", {})
    template_key = "template_female_path" if gender == "female" else "template_male_path"
    template_path = Path(blender_cfg.get(template_key, f"templates/template_{gender}.blend"))
    output_dir = WORKSPACE_DIR / target_name / "reskin"

    result = ai_reskin_runner.generate_reskin(
        template_path=template_path,
        output_dir=output_dir,
        model_name=target_name,
        prompt=prompt,
        skin_prompt=skin_prompt,
        num_cameras=num_cameras,
        texture_size=texture_size,
        controlnet_conditioning_scale=controlnet_conditioning_scale,
        steps=steps,
        timeout_s=blender_cfg.get("timeout_seconds", 900),
    )

    return {
        "target_name": target_name,
        "status": result.status,
        "mock": result.mock,
        "output_dir": str(result.output_dir),
        "produced_files": [str(p) for p in result.produced_files],
        "log_tail": result.log[-2000:],
        "validation": {
            "ok": result.status in ("generated", "mock"),
            "errors": [] if result.status in ("generated", "mock") else [result.log[-500:]],
            "warnings": [] if result.status == "generated" else [
                "mock mode: no real texture produced" if result.mock else "pipeline reported an error"
            ],
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

    # Same wiring gap as compile_gtav_ped: the .ymt was only ever emitted as
    # CW-XML here, never converted to the binary CPedVariationInfo the game
    # reads (see docs/architecture-decisions.md point 11 for the real,
    # round-trip-verified schema/CLI this calls).
    conv_result = codewalker_convert.convert_xml_to_binary(
        xml_dir=compiled_dir, output_dir=compiled_dir, model_name=target_name, extensions=(".ymt",))
    ymt_binary_conversion = {
        "status": conv_result.status,
        "produced_files": conv_result.produced_files,
        "message": conv_result.message,
    }

    xml_manifest_updater.add_ped_to_manifest(
        manifest_path, target_name, gender=gender, is_streamed_gfx=is_streamed_gfx, ped_type=ped_type)

    manifest_report = validator.validate_manifest_xml(manifest_path, target_name)

    return {
        "target_name": target_name,
        "manifest_path": str(manifest_path),
        "ymt_path": str(ymt_path),
        "ymt_binary_conversion": ymt_binary_conversion,
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
