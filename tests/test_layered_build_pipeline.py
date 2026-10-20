"""Wiring of the layered-ped build chain: industrialize_pipeline (staleness,
failure surfacing), blender_runner.build_layered_ped (command, success
signal) and the build_layered_ped MCP tool (conversion -> LLM sliders ->
Blender, stopping early on failure). No Blender/CLI/Ollama needed."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server import blender_runner, prompt_refiner, server
from scripts import industrialize_pipeline as ip
from scripts.codewalker_convert import ConversionResult, TreeExportResult


# --- industrialize_pipeline ---------------------------------------------------

@pytest.fixture
def fake_cli(monkeypatch):
    calls = []

    def fake_tree(source_root, output_root):
        calls.append(source_root.name)
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "x.ydd.xml").write_text("<x/>")
        return TreeExportResult(converted=1)

    def fake_one(binary_path, output_dir):
        (output_dir / f"{binary_path.name}.xml").write_text("<Fragment/>")
        return ConversionResult(status="converted")

    monkeypatch.setattr(ip, "_resolve_cli_path", lambda: "/fake/cli")
    monkeypatch.setattr(ip, "export_tree_to_xml", fake_tree)
    monkeypatch.setattr(ip, "export_binary_to_xml", fake_one)
    return calls


def test_industrialize_converts_links_and_skips_unchanged_trees(tmp_path, fake_cli):
    imp, xml = tmp_path / "import", tmp_path / "import_xml"
    (imp / "CatalinaNude").mkdir(parents=True)
    (imp / "CatalinaNude" / "uppr_000_r.ydd").write_bytes(b"raw")
    (imp / "blender").mkdir()
    (imp / "blender" / "t.blend").write_bytes(b"not a rage file")
    (imp / "CatalinaNude.yft").write_bytes(b"raw")

    first = ip.industrialize(imp, xml)
    assert first["converted"] == ["CatalinaNude"]
    assert (xml / "CatalinaNude").resolve() == (imp / "CatalinaNude_xml").resolve()
    assert not (xml / "blender").exists()
    assert (xml / "CatalinaNude.yft.xml").is_file()

    second = ip.industrialize(imp, xml)
    assert second["up_to_date"] == ["CatalinaNude"] and fake_cli == ["CatalinaNude"]


def test_industrialize_reconverts_when_a_source_is_newer(tmp_path, fake_cli):
    imp, xml = tmp_path / "import", tmp_path / "import_xml"
    (imp / "P").mkdir(parents=True)
    src = imp / "P" / "head_000_r.ydd"
    src.write_bytes(b"raw")
    ip.industrialize(imp, xml)
    stamp = imp / "P_xml" / ip.STAMP_NAME
    os.utime(src, (stamp.stat().st_mtime + 10, stamp.stat().st_mtime + 10))

    assert ip.industrialize(imp, xml)["converted"] == ["P"]


def test_industrialize_raises_on_failed_conversion(tmp_path, monkeypatch, fake_cli):
    imp = tmp_path / "import"
    (imp / "P").mkdir(parents=True)
    (imp / "P" / "head_000_r.ydd").write_bytes(b"raw")
    monkeypatch.setattr(ip, "export_tree_to_xml",
                        lambda s, o: TreeExportResult(failed=[(str(s), "boom")]))
    with pytest.raises(ip.IndustrializeError):
        ip.industrialize(imp, tmp_path / "import_xml")
    assert not (imp / "P_xml" / ip.STAMP_NAME).exists()  # failed tree must be retried next run


# --- blender_runner.build_layered_ped ----------------------------------------

def test_runner_passes_morph_spec_and_requires_real_output(tmp_path, monkeypatch):
    template = tmp_path / "t.blend"
    template.write_bytes(b"blend")
    out = tmp_path / "out" / "P.blend"
    out.parent.mkdir()
    out.write_bytes(b"stale from a previous run")
    monkeypatch.setattr(blender_runner, "_resolve_blender_path", lambda: "/fake/blender")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=1, stdout="[WARN] something\nTraceback", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = blender_runner.build_layered_ped(template, tmp_path, "P", out, bust_size="80A", glutes="round",
                                              glute_height="low")

    cmd = seen["cmd"]
    assert cmd[cmd.index("--bust-size") + 1] == "80A"
    assert cmd[cmd.index("--glutes") + 1] == "round"
    assert cmd[cmd.index("--glute-height") + 1] == "low"
    assert "--python-exit-code" in cmd
    assert result.status == "error" and not out.exists()  # stale file removed, failure reported
    assert result.warnings == ["[WARN] something"]


def test_runner_requires_the_deformation_file_when_asked(tmp_path, monkeypatch):
    template = tmp_path / "t.blend"
    template.write_bytes(b"blend")
    out, deform = tmp_path / "P.blend", tmp_path / "P_deformation.json"
    deform.write_text("{} stale")
    monkeypatch.setattr(blender_runner, "_resolve_blender_path", lambda: "/fake/blender")
    seen = {}

    def fake_run(cmd, **kw):  # Blender "succeeds" but writes only the .blend
        seen["cmd"] = cmd
        out.write_bytes(b"blend")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = blender_runner.build_layered_ped(template, tmp_path, "P", out, deformation_out=deform)

    assert seen["cmd"][seen["cmd"].index("--deformation-out") + 1] == str(deform)
    assert result.status == "error" and not deform.exists()


# --- MCP tool -------------------------------------------------------------------

@pytest.fixture
def tool_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(server.industrialize_pipeline, "industrialize", lambda: {"converted": []})
    built = {}

    def fake_build(**kw):
        built.update(kw)
        if kw.get("deformation_out"):  # the real runner only reports "built" once it exists
            kw["deformation_out"].write_text(json.dumps({"ped_name": kw["ped_name"], "targets": {
                "bra_size": kw["bust_size"], "glutes": kw["glutes"], "glute_height": kw["glute_height"]}}))
        return blender_runner.LayeredBuildResult(status="built", output_blend=kw["output_blend"])

    monkeypatch.setattr(server.blender_runner, "build_layered_ped", fake_build)
    return built


def test_tool_feeds_the_body_targets_into_blender(tool_env, monkeypatch):
    monkeypatch.setattr(server.prompt_refiner, "infer_body_targets",
                        lambda d: prompt_refiner.BodyTargets("80A", "round", "high", None))
    res = server.build_layered_ped("CatalinaNude", "bonnet 80A, fesses bombées et hautes")

    assert res["status"] == "built"
    assert (res["bra_size"], res["glutes"], res["glute_height"]) == ("80A", "round", "high")
    assert (tool_env["bust_size"], tool_env["glutes"], tool_env["glute_height"]) == ("80A", "round", "high")
    assert tool_env["ped_name"] == "CatalinaNude"


def test_tool_never_builds_an_unchanged_ped_for_a_reshape_request(tool_env, monkeypatch):
    def fail(d):
        raise prompt_refiner.PromptRefinerError("ollama down")
    monkeypatch.setattr(server.prompt_refiner, "infer_body_targets", fail)

    assert server.build_layered_ped("CatalinaNude", "petite poitrine")["status"] == "error"
    assert tool_env == {}


# --- MCP tools: package_layered_ped / update_packaged_ped --------------------------

@pytest.fixture
def ship_env(tool_env, tmp_path, monkeypatch):
    """Fake package_ped/build_oiv that write real files, so publishing is exercised."""
    dist = tmp_path / "dist"
    monkeypatch.setattr(server, "DELIVERY_OUTPUT_DIR", dist)
    usermods = tmp_path / "UserMods"
    usermods.mkdir()
    monkeypatch.setitem(server._CONFIG, "usermods_ap_m_xml", str(usermods / "ap_m.xml"))  # never the real one
    calls = {}

    def fake_package(deformation, source, name, out, look=None):
        calls["package"] = (deformation, source, name, out)
        calls["look"] = look
        (out / name).mkdir()
        (out / name / "uppr_000_r.ydd").write_text(f"body {calls.get('tag')}")
        for ext in ("yft", "ymt"):
            (out / f"{name}.{ext}").write_text(ext)
        return {"output": [str(out / f"{name}.yft"), str(out / f"{name}.ymt"), str(out / name)],
                "reshaped": {"uppr_000_r.ydd": 10}, "unchanged_components": [], "unreshaped_skin_variants": [],
                "variants": [{"file": "uppr_002_r.ydd", "bust": None, "glutes": None, "reached": False}],
                "seams": []}

    def fake_oiv(ped_dir, name, out):
        calls["oiv"] = (ped_dir, name, out)
        (out / f"{name}.oiv").write_text("oiv")
        return out / f"{name}.oiv"

    monkeypatch.setattr(server.ped_package, "package_ped", fake_package)
    monkeypatch.setattr(server.oiv_package, "build_oiv", fake_oiv)
    return SimpleNamespace(dist=dist, calls=calls, built=tool_env, manifest=usermods / "ap_m.xml")


def _record(tmp_path, name, targets, source="CatalinaNude"):
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / name / f"{name}_deformation.json").write_text(
        json.dumps({"ped_name": source, "targets": targets}))


def test_package_tool_refuses_an_existing_ped_before_building(ship_env):
    (ship_env.dist / "Eva").mkdir(parents=True)
    res = server.package_layered_ped("CatalinaNude", "Eva", "bonnet 85C")
    assert res["status"] == "error" and "already exists" in res["error"]
    assert ship_env.built == {}  # Blender never ran


def test_package_tool_chains_build_package_and_oiv(ship_env, tmp_path, monkeypatch):
    monkeypatch.setattr(server.prompt_refiner, "infer_body_targets",
                        lambda d: prompt_refiner.BodyTargets("85C", "athletic", None, None))
    res = server.package_layered_ped("CatalinaNude", "Eva", "bonnet 85C, fesses athlétiques")

    built, calls, dist = ship_env.built, ship_env.calls, ship_env.dist
    assert built["ped_name"] == "CatalinaNude" and built["deformation_out"].name == "Eva_deformation.json"
    assert calls["package"][1:] == (server.industrialize_pipeline.IMPORT_DIR, "Eva", calls["oiv"][0])
    assert res["status"] == "packaged" and res["oiv"] == str(dist / "Eva.oiv")
    assert sorted(p.name for p in dist.iterdir()) == ["Eva", "Eva.oiv", "Eva.yft", "Eva.ymt"]  # staging gone
    assert (tmp_path / "Eva").is_dir() and not list(tmp_path.glob(".Eva*"))
    assert any("uppr_002_r.ydd" in w for w in res["warnings"])  # a missed variant is surfaced
    assert res["registration"].startswith("registered")
    assert server.xml_manifest_updater.list_registered_peds(ship_env.manifest) == ["Eva"]


def test_update_changes_only_the_named_target_and_replaces_the_ped(ship_env, tmp_path, monkeypatch):
    _record(tmp_path, "Eva", {"bra_size": "75C", "glutes": "slim", "glute_height": None})
    (ship_env.dist / "Eva").mkdir(parents=True)
    (ship_env.dist / "Eva" / "uppr_000_r.ydd").write_text("old body")
    (ship_env.dist / "Eva" / "stale_variant.ydd").write_text("old")
    monkeypatch.setattr(server.prompt_refiner, "infer_body_targets",
                        lambda d: prompt_refiner.BodyTargets("95C", None, None, None))
    ship_env.calls["tag"] = "95C"

    res = server.update_packaged_ped("Eva", "bonnet 95C")

    assert res["status"] == "packaged", res
    assert (ship_env.built["bust_size"], ship_env.built["glutes"]) == ("95C", "slim")  # glutes kept
    assert ship_env.built["ped_name"] == "CatalinaNude"  # rebuilt from the source, not from Eva
    assert (ship_env.dist / "Eva" / "uppr_000_r.ydd").read_text() == "body 95C"
    assert not (ship_env.dist / "Eva" / "stale_variant.ydd").exists()  # whole folder replaced
    assert res["previous_targets"]["bra_size"] == "75C"
    assert res["registration"].startswith("registered")  # an update also registers a ped missing from ap_m.xml
    _record(tmp_path, "Eva", {"bra_size": "95C", "glutes": "slim", "glute_height": None})  # what Blender writes
    assert server.update_packaged_ped("Eva", "bonnet 95C")["registration"].startswith("already registered")


def test_failed_update_leaves_the_previous_ped_untouched(ship_env, tmp_path, monkeypatch):
    _record(tmp_path, "Eva", {"bra_size": "75C", "glutes": "slim", "glute_height": None})
    (ship_env.dist / "Eva").mkdir(parents=True)
    (ship_env.dist / "Eva" / "uppr_000_r.ydd").write_text("old body")
    monkeypatch.setattr(server.prompt_refiner, "infer_body_targets",
                        lambda d: prompt_refiner.BodyTargets("95C", None, None, None))
    monkeypatch.setattr(server.blender_runner, "build_layered_ped",
                        lambda **kw: blender_runner.LayeredBuildResult(status="error", output_blend=kw["output_blend"]))

    res = server.update_packaged_ped("Eva", "bonnet 95C")

    assert res["status"] == "error"
    assert (ship_env.dist / "Eva" / "uppr_000_r.ydd").read_text() == "old body"
    assert json.loads((tmp_path / "Eva" / "Eva_deformation.json").read_text())["targets"]["bra_size"] == "75C"
    assert not list(ship_env.dist.glob(".Eva*")) and not list(tmp_path.glob(".Eva*"))
    assert not ship_env.manifest.exists()  # nothing registered for a failed build


def test_update_needs_a_recorded_build(ship_env):
    res = server.update_packaged_ped("Nobody", "bonnet 95C")
    assert res["status"] == "error" and "no recorded build" in res["error"]
    assert ship_env.built == {}


def test_look_is_validated_before_blender_then_built_packaged_recorded_and_kept(ship_env, tmp_path, monkeypatch):
    imp, imp_xml = tmp_path / "import", tmp_path / "import_xml"
    for rel in ("Donor/head_002_r.ydd", "Donor/hair_004_u.ydd", "Donor/hair_001_u.ydd", "CatalinaNude/feet_002_r.ydd"):
        (imp / rel).parent.mkdir(parents=True, exist_ok=True)
        (imp / rel).write_bytes(b"ydd")
    (imp_xml / "CatalinaNude").mkdir(parents=True)
    (imp_xml / "CatalinaNude" / "feet_diff_002_f_whi.ytd.xml").write_text(
        "<TextureDictionary><Item><FileName>mp_fm_skin_f_fe_whi.dds</FileName></Item></TextureDictionary>")
    monkeypatch.setattr(server.industrialize_pipeline, "IMPORT_DIR", imp)
    monkeypatch.setattr(server.industrialize_pipeline, "IMPORT_XML_DIR", imp_xml)
    monkeypatch.setitem(server._CONFIG, "hair_pool", ["Donor/hair_004_u.ydd"])
    monkeypatch.setattr(server.prompt_refiner, "infer_body_targets",
                        lambda d: prompt_refiner.BodyTargets("85C", None, None, None))

    bad = server.package_layered_ped("CatalinaNude", "Eva", "bonnet 85C", head="../etc/passwd")
    assert bad["status"] == "error" and ship_env.built == {}  # refused before Blender

    res = server.package_layered_ped("CatalinaNude", "Eva", "bonnet 85C", head="Donor/head_002_r.ydd")
    look = {"head": "Donor/head_002_r.ydd", "hair": "Donor/hair_004_u.ydd",  # random style, all colours
            "feet": "CatalinaNude/feet_002_r.ydd"}                              # barefoot by default
    assert res["status"] == "packaged" and res["look"] == look and ship_env.calls["look"] == look
    assert ship_env.built["overrides"]["hair"] == (imp_xml / "Donor" / "hair_004_u.ydd.xml", None)
    record = json.loads((tmp_path / "Eva" / "Eva_deformation.json").read_text())
    assert record["look"] == look  # kept by updates

    ship_env.calls.clear()
    res = server.update_packaged_ped("Eva", hair="Donor/hair_001_u.ydd:b")
    assert ship_env.calls["look"] == {**look, "hair": "Donor/hair_001_u.ydd:b"}  # only the hair changed
    assert server.update_packaged_ped("Nobody", hair="Donor/hair_001_u.ydd:b")["status"] == "error"


def test_a_record_from_before_looks_keeps_its_head(tmp_path, monkeypatch):
    monkeypatch.setattr(server.industrialize_pipeline, "IMPORT_XML_DIR", tmp_path)
    old = {"targets": {"bra_size": "95C", "head": str(tmp_path / "Donor" / "head_000_r.ydd.xml")}}
    assert server._recorded_look(old) == {"head": "Donor/head_000_r.ydd"}
    assert server._recorded_look({"targets": {}}) == {}


def test_blender_runs_without_the_wslg_display(monkeypatch):
    # WSLg's display hangs Blender at startup on about half the runs
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    env = blender_runner.headless_env()
    assert "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env and env["PATH"]
