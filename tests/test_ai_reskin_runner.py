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


def test_skin_prompt_is_forwarded_to_blender_cli(tmp_path: Path, monkeypatch):
    """Per-body-region prompting (docs/architecture-decisions.md point 15):
    an explicit skin_prompt must reach scripts/ai_texture_reskin.py as
    --skin-prompt, not get silently dropped at the subprocess boundary.
    """
    monkeypatch.setattr(ai_reskin_runner, "_resolve_blender_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(ai_reskin_runner, "_resolve_sd_python", lambda: "/usr/bin/true")
    template = tmp_path / "template.blend"
    template.write_text("fake")

    captured_cmd = {}

    class FakeCompletedProcess:
        returncode = 0
        stdout = "[OK]"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return FakeCompletedProcess()

    monkeypatch.setattr(ai_reskin_runner.subprocess, "run", fake_run)

    ai_reskin_runner.generate_reskin(
        template_path=template,
        output_dir=tmp_path / "reskin",
        model_name="TestPed01",
        prompt="a dark leather jacket",
        skin_prompt="natural skin tone",
    )

    cmd = captured_cmd["cmd"]
    assert "--skin-prompt" in cmd
    assert cmd[cmd.index("--skin-prompt") + 1] == "natural skin tone"


def test_no_skin_prompt_flag_when_not_given(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ai_reskin_runner, "_resolve_blender_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(ai_reskin_runner, "_resolve_sd_python", lambda: "/usr/bin/true")
    template = tmp_path / "template.blend"
    template.write_text("fake")

    captured_cmd = {}

    class FakeCompletedProcess:
        returncode = 0
        stdout = "[OK]"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return FakeCompletedProcess()

    monkeypatch.setattr(ai_reskin_runner.subprocess, "run", fake_run)

    ai_reskin_runner.generate_reskin(
        template_path=template,
        output_dir=tmp_path / "reskin",
        model_name="TestPed01",
        prompt="a dark leather jacket",
    )

    assert "--skin-prompt" not in captured_cmd["cmd"]
