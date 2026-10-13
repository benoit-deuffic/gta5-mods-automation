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
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server import ai_reskin_runner, blender_runner, prompt_refiner, validator
from mcp_server.text3d_backend import get_backend
from scripts import (codewalker_convert, industrialize_pipeline, oiv_package, package_builder, ped_package,
                     xml_manifest_updater, ymt_builder)

mcp = FastMCP("gtav-ped-mcp")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
    """Submit a prompt to the configured text-3D backend (Tripo3D by default,
    or a local backend via TEXT3D_BACKEND=local), poll until done, download
    the generated mesh + textures to a staging dir.

    `art_style` is accepted for spec compatibility but ignored (not a
    Tripo3D parameter). Runs in mock mode automatically when no backend
    credentials/tooling are available.
    """
    target_name = _normalize_target_name(target_name)
    staging_dir = _staging_dir(target_name)
    backend_name = os.environ.get("TEXT3D_BACKEND", "tripo").lower()
    if backend_name == "tripo":
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
        pose_mode=_CONFIG.get("tripo", {}).get("pose_mode", "a-pose"),
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
def build_layered_ped(ped_name: str, body_description: str = "") -> dict:
    """Build a ped from the user's raw extracted assets under import/ into a
    rigged, textured .blend (+ preview PNG), optionally reshaping the bust
    and/or the buttocks from a free-text description.

    Chain: scripts/industrialize_pipeline.industrialize() (binary -> XML,
    import_xml/, ped skeleton) -> prompt_refiner.infer_body_targets(
    body_description) when a description is given (e.g. "bonnet 80A,
    fesses bombées et hautes" -> bra size "80A", glutes "round", glute
    height "high") ->
    blender_runner.build_layered_ped() (scripts/import_layered_ped.py),
    which MEASURES the body and fits the bust circumference / the glute
    proportions (lateral and posterior waist-to-hip ratios, apex height) to
    those targets (no base size to declare).
    `ped_name` is the folder name under import/ (case-sensitive, e.g.
    "CatalinaNude"). Stops before Blender if the conversion or the size
    extraction fails -- an unchanged ped must never be returned for a
    reshape request. `measurements` holds the measured before/after lines.
    """
    try:
        targets = _infer_targets(body_description)
    except prompt_refiner.PromptRefinerError as exc:
        return {"ped_name": ped_name, "status": "error", "error": f"body target extraction: {exc}"}
    return _build_layered(ped_name, targets, WORKSPACE_DIR / ped_name, ped_name)


@mcp.tool()
def package_layered_ped(ped_name: str, new_name: str, body_description: str = "") -> dict:
    """Build, reshape and ship a ped as a new ped `new_name`, ready to install:
    build_layered_ped's chain (with every body variant fitted) ->
    scripts/ped_package.py (original binaries renamed, only the reshaped .ydd
    regenerated, on every LOD) -> scripts/oiv_package.py (OpenIV package
    adding the files into dlcpacks/pedselector/dlc.rpf/peds.rpf).

    Output in DELIVERY_OUTPUT_DIR: `<new_name>.yft`, `<new_name>.ymt`,
    `<new_name>/` and `<new_name>.oiv`, and workspace/<new_name>/ (.blend,
    preview, deformation record); never overwrites an existing ped (checked
    before Blender runs; use update_packaged_ped for that). Still to do by
    hand: install the .oiv with OpenIV's Package Installer (mods folder) and
    register `new_name` in UserMods/ap_m.xml (PedSelector, Streamed = true).
    `report` lists every reshaped file, variant measurements and seam checks
    ([WARN] = a variant misses its targets or a seam opened).
    """
    try:
        ped_package.check_output(new_name, DELIVERY_OUTPUT_DIR)
        targets = _infer_targets(body_description)
    except (ped_package.PackageError, prompt_refiner.PromptRefinerError) as exc:
        return {"ped_name": ped_name, "new_name": new_name, "status": "error", "error": str(exc)}
    return _ship(ped_name, new_name, targets)


@mcp.tool()
def update_packaged_ped(name: str, body_description: str) -> dict:
    """Change the body of a ped already shipped by package_layered_ped, e.g.
    ("Eva", "bonnet 95C"): only the targets the description names change,
    the others are kept from the ped's recorded request
    (workspace/<name>/<name>_deformation.json). The ped is rebuilt from its
    ORIGINAL source ped (never by reshaping the shipped one again, which would
    compound deformations), staged, and only replaces dist/<name>* and
    workspace/<name>/ once the whole build + package succeeded -- a failed
    update leaves the previous ped untouched. Reinstall the new .oiv over the
    old one (same package id, OpenIV `add` replaces the files); ap_m.xml is
    unchanged.
    """
    record_path = WORKSPACE_DIR / name / f"{name}_deformation.json"
    try:
        record = json.loads(record_path.read_text())
        previous = record["targets"]
        requested = _infer_targets(body_description)
    except (OSError, ValueError, KeyError) as exc:
        return {"new_name": name, "status": "error",
                "error": f"no recorded build for {name} ({record_path}): {exc!r}"}
    except prompt_refiner.PromptRefinerError as exc:
        return {"new_name": name, "status": "error", "error": f"body target extraction: {exc}"}
    targets = prompt_refiner.BodyTargets(
        bra_size=requested.bra_size or previous["bra_size"],
        glutes=requested.glutes or previous["glutes"],
        glute_height=requested.glute_height or previous["glute_height"],
        model=requested.model)
    return {**_ship(record["ped_name"], name, targets), "previous_targets": previous}


def _infer_targets(body_description: str) -> prompt_refiner.BodyTargets | None:
    return prompt_refiner.infer_body_targets(body_description) if body_description else None


def _ship(ped_name: str, name: str, targets) -> dict:
    """Build + package + .oiv into staging dirs, then publish them over
    workspace/<name>/ and dist/<name>*. Nothing published is touched unless
    every step succeeded."""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    DELIVERY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=WORKSPACE_DIR, prefix=f".{name}."))
    staged = Path(tempfile.mkdtemp(dir=DELIVERY_OUTPUT_DIR, prefix=f".{name}."))
    try:
        deformation = work / f"{name}_deformation.json"
        build = _build_layered(ped_name, targets, work, name, deformation_out=deformation)
        result = {**build, "new_name": name}
        if build["status"] != "built":
            return result
        try:
            report = ped_package.package_ped(deformation, industrialize_pipeline.IMPORT_DIR, name, staged)
            oiv_package.build_oiv(staged, name, staged)
        except (ped_package.PackageError, oiv_package.OivError) as exc:
            return {**result, "status": "error", "error": f"packaging: {exc}"}
        for entry in (f"{name}.yft", f"{name}.ymt", name, f"{name}.oiv"):
            _publish(staged / entry, DELIVERY_OUTPUT_DIR / entry)
        _publish(work, WORKSPACE_DIR / name)
        lines = [line.replace(str(staged), str(DELIVERY_OUTPUT_DIR)) for line in ped_package.report_lines(report)]
        registration = _register(name)
        return {**result, "status": "packaged", "registration": registration,
                "output_blend": str(WORKSPACE_DIR / name / f"{name}.blend"),
                "preview_png": str(WORKSPACE_DIR / name / f"{name}_preview.png") if build["preview_png"] else None,
                "oiv": str(DELIVERY_OUTPUT_DIR / f"{name}.oiv"),
                "files": [str(DELIVERY_OUTPUT_DIR / Path(f).name) for f in report["output"]],
                "report": lines,
                "warnings": build["warnings"] + [line for line in lines if line.startswith("[WARN]")]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)


def _register(name: str) -> str:
    """Add the shipped (streamed) ped to the game's UserMods/ap_m.xml
    (config `usermods_ap_m_xml`); an existing entry is never rewritten."""
    manifest = _CONFIG.get("usermods_ap_m_xml")
    if not manifest:
        return "skipped: no usermods_ap_m_xml in config/pipeline.json"
    if not Path(manifest).parent.is_dir():
        return f"skipped: UserMods folder not found ({Path(manifest).parent})"
    try:
        return f"{xml_manifest_updater.register_ped(Path(manifest), name)} in {manifest}"
    except (OSError, ValueError, ET.ParseError) as exc:  # ManifestError / defusedxml errors are ValueErrors
        return f"error: {exc}"


def _publish(staged: Path, dest: Path) -> None:
    """Move `staged` to `dest`, replacing it; the old one is removed only
    after the new one is in place."""
    old = dest.with_name(f".{dest.name}.old")
    _remove(old)
    if dest.exists():
        dest.rename(old)
    staged.rename(dest)
    _remove(old)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _build_layered(ped_name: str, targets, out_dir: Path, stem: str,
                   deformation_out: Path | None = None) -> dict:
    """build_layered_ped's chain, writing <out_dir>/<stem>.blend (+ preview)."""
    try:
        conversion = industrialize_pipeline.industrialize()
    except industrialize_pipeline.IndustrializeError as exc:
        return {"ped_name": ped_name, "status": "error", "error": f"conversion: {exc}"}

    blender_cfg = _CONFIG.get("blender", {})
    result = blender_runner.build_layered_ped(
        template_path=PROJECT_ROOT / blender_cfg["layered_template_path"],
        skeleton_yft=PROJECT_ROOT / blender_cfg["layered_skeleton_yft"],
        ped_root=industrialize_pipeline.IMPORT_XML_DIR,
        ped_name=ped_name,
        output_blend=out_dir / f"{stem}.blend",
        bust_size=targets.bra_size if targets else None,
        glutes=targets.glutes if targets else None,
        glute_height=targets.glute_height if targets else None,
        preview_png=out_dir / f"{stem}_preview.png",
        deformation_out=deformation_out,
        timeout_s=blender_cfg.get("layered_timeout_seconds", 900),
    )
    return {
        "ped_name": ped_name,
        "status": result.status,
        "output_blend": str(result.output_blend) if result.status == "built" else None,
        "preview_png": str(result.preview_png) if result.preview_png else None,
        "conversion": conversion,
        "bra_size": targets.bra_size if targets else None,
        "glutes": targets.glutes if targets else None,
        "glute_height": targets.glute_height if targets else None,
        "targets_model": targets.model if targets else None,
        "measurements": result.measurements,
        "warnings": result.warnings,
        "log_tail": result.log[-2000:] if result.status != "built" else "",
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
    compile_gtav_ped -- it does not produce a new rigged mesh. It reskins
    EVERY body component found on the ped template (uppr/lowr/hair/etc, not
    just one), each as its own output file
    "<target_name>_<component>_reskin.png" in the output dir (e.g.
    "testped01_uppr_reskin.png", "testped01_lowr_reskin.png") -- real
    Sollumz imports of a multi-component ped only pre-skin ONE Drawable
    (usually the head), so the pipeline first auto-rigs every other
    component onto the same armature before texturing them (see
    scripts/blender_rig_export.rig_unskinned_body_components(), added
    2026-10-11 after discovering the rest of the body geometry was already
    present but unskinned -- no extra extraction from the game was needed).
    Round trip to a real binary .ytd is a separate manual step via
    scripts/dds_pack_and_validate.py per produced component (not run
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
