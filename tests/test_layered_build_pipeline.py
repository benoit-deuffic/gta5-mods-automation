"""Wiring of the layered-ped build chain: industrialize_pipeline (staleness,
failure surfacing), blender_runner.build_layered_ped (command, success
signal) and the build_layered_ped MCP tool (conversion -> LLM sliders ->
Blender, stopping early on failure). No Blender/CLI/Ollama needed."""
from __future__ import annotations

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


# --- MCP tool -------------------------------------------------------------------

@pytest.fixture
def tool_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(server.industrialize_pipeline, "industrialize", lambda: {"converted": []})
    built = {}

    def fake_build(**kw):
        built.update(kw)
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
