"""Regression test for a real wiring gap found during a full end-to-end run
(2026-10-11): compile_gtav_ped and sync_usermods_manifest produced CW-XML
(.ydd.xml/.yft.xml/.ytd.xml/.ymt.xml) but never called
scripts/codewalker_convert.py, so the delivery zip only ever shipped XML
intermediates, never the binary .ydd/.yft/.ytd/.ymt the game actually loads.
codewalker_convert.convert_xml_to_binary() itself was already covered by
tests/test_codewalker_cli_integration.py (skipped without a built CLI) -- this
test instead locks in that mcp_server.server's TOOLS call it, using a fake
stand-in so it runs unconditionally (no CLI/Blender needed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server import server


class _FakeConversionResult:
    def __init__(self, produced):
        self.status = "converted"
        self.produced_files = produced
        self.message = "[fake] converted"


def test_compile_gtav_ped_calls_binary_conversion_after_real_compile(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "WORKSPACE_DIR", tmp_path)

    class _FakeCompileResult:
        status = "compiled"
        mock = False
        output_dir = tmp_path / "testped01" / "compiled"
        produced_files = [output_dir / "testped01.yft.xml"]
        log = "ok"

    def fake_compile_ped(**kwargs):
        out = kwargs["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "testped01.yft.xml").write_text("<Fragment/>")
        result = _FakeCompileResult()
        result.output_dir = out
        result.produced_files = [out / "testped01.yft.xml"]
        return result

    calls = {}

    def fake_convert(xml_dir, output_dir, model_name, extensions=None):
        (output_dir / f"{model_name}.yft").write_bytes(b"\x00" * 10)
        calls["xml_dir"] = xml_dir
        calls["model_name"] = model_name
        return _FakeConversionResult([str(output_dir / f"{model_name}.yft")])

    monkeypatch.setattr(server.blender_runner, "compile_ped", fake_compile_ped)
    monkeypatch.setattr(server.codewalker_convert, "convert_xml_to_binary", fake_convert)

    result = server.compile_gtav_ped(target_name="TestPed01", fbx_path="/tmp/doesnotmatter.fbx")

    assert calls["model_name"] == "testped01"
    assert result["binary_conversion"]["status"] == "converted"
    assert any(p.endswith(".yft") and not p.endswith(".xml") for p in result["produced_files"])


def test_compile_gtav_ped_skips_binary_conversion_when_mock(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "WORKSPACE_DIR", tmp_path)

    class _FakeMockResult:
        status = "mock"
        mock = True
        output_dir = tmp_path / "testped01" / "compiled"
        produced_files = []
        log = "[MOCK]"

    def fake_compile_ped(**kwargs):
        out = kwargs["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        return _FakeMockResult()

    called = {"n": 0}

    def fake_convert(*args, **kwargs):
        called["n"] += 1
        return _FakeConversionResult([])

    monkeypatch.setattr(server.blender_runner, "compile_ped", fake_compile_ped)
    monkeypatch.setattr(server.codewalker_convert, "convert_xml_to_binary", fake_convert)

    result = server.compile_gtav_ped(target_name="TestPed01", fbx_path="/tmp/doesnotmatter.fbx")

    assert result["binary_conversion"] is None
    assert called["n"] == 0


def test_sync_usermods_manifest_converts_ymt_to_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "WORKSPACE_DIR", tmp_path)

    calls = {"n": 0}

    def fake_convert(xml_dir, output_dir, model_name, extensions=None):
        calls["n"] += 1
        (output_dir / f"{model_name}.ymt").write_bytes(b"\x00" * 10)
        return _FakeConversionResult([str(output_dir / f"{model_name}.ymt")])

    monkeypatch.setattr(server.codewalker_convert, "convert_xml_to_binary", fake_convert)

    result = server.sync_usermods_manifest(target_name="TestPed01")

    assert calls["n"] >= 1
    assert result["ymt_binary_conversion"]["status"] == "converted"
    assert result["registered_peds"] == ["testped01"]
