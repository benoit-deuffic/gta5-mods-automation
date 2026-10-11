from pathlib import Path

from mcp_server import ai_reskin_runner


def test_mock_fallback_when_blender_and_sd_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ai_reskin_runner, "_resolve_blender_path", lambda: None)
    monkeypatch.setattr(ai_reskin_runner, "_resolve_sd_python", lambda: None)

    result = ai_reskin_runner.generate_reskin(
        template_path=tmp_path / "missing.blend",
        output_dir=tmp_path / "reskin",
        model_name="TestPed01",
        prompt="a stylized adult character wearing a dark jacket",
    )
    assert result.mock is True
    assert result.status == "mock"
    assert len(result.produced_files) == 1
    assert "[MOCK]" in result.produced_files[0].read_text()


def test_mock_fallback_when_template_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ai_reskin_runner, "_resolve_blender_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(ai_reskin_runner, "_resolve_sd_python", lambda: "/usr/bin/true")

    result = ai_reskin_runner.generate_reskin(
        template_path=tmp_path / "missing_template.blend",
        output_dir=tmp_path / "reskin",
        model_name="TestPed01",
        prompt="a stylized adult character wearing a dark jacket",
    )
    assert result.mock is True
    assert "Template not found" in result.log


def test_is_pipeline_available_reflects_resolution(monkeypatch):
    monkeypatch.setattr(ai_reskin_runner, "_resolve_blender_path", lambda: "/usr/bin/blender")
    monkeypatch.setattr(ai_reskin_runner, "_resolve_sd_python", lambda: None)
    assert ai_reskin_runner.is_pipeline_available() is False

    monkeypatch.setattr(ai_reskin_runner, "_resolve_sd_python", lambda: "/usr/bin/python3")
    assert ai_reskin_runner.is_pipeline_available() is True
