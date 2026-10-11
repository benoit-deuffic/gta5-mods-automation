"""Integration test for scripts/codewalker_convert.py against the REAL
CodeWalker-Core-based CLI (tools/CodeWalkerCli), not a mock.

Skipped automatically when the CLI hasn't been built (CODEWALKER_CLI_PATH
unset/missing and no `codewalker-cli` on PATH) -- e.g. on a fresh checkout
before running scripts/fetch_codewalker.sh + `dotnet publish`. On this
sandbox it was verified to pass for real on 2026-10-11 (see
docs/architecture-decisions.md point 10).
"""
import os
import shutil
from pathlib import Path

import pytest

from scripts import codewalker_convert, ymt_builder

CLI_PATH = os.environ.get("CODEWALKER_CLI_PATH", "").strip() or shutil.which("codewalker-cli")

pytestmark = pytest.mark.skipif(
    not CLI_PATH or not Path(CLI_PATH).exists(),
    reason="codewalker-cli not built; run scripts/fetch_codewalker.sh + "
           "dotnet publish tools/CodeWalkerCli -c Release -r linux-x64 "
           "--self-contained true -o tools/CodeWalkerCli/publish",
)


@pytest.fixture
def cli_env(monkeypatch):
    monkeypatch.setenv("CODEWALKER_CLI_PATH", CLI_PATH)


def _write_minimal_cwxml(xml_dir: Path, model_name: str) -> None:
    (xml_dir / f"{model_name}.ytd.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<TextureDictionary/>', encoding="utf-8")
    (xml_dir / f"{model_name}.ydd.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<DrawableDictionary/>', encoding="utf-8")
    (xml_dir / f"{model_name}.yft.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<Fragment/>', encoding="utf-8")
    ymt_builder.write_ymt_xml(xml_dir / f"{model_name}.ymt.xml", model_name)


def test_real_cli_converts_ytd_ydd_yft_to_binary(cli_env, tmp_path: Path):
    xml_dir = tmp_path / "xml"
    out_dir = tmp_path / "out"
    xml_dir.mkdir()
    _write_minimal_cwxml(xml_dir, "TestPed01")

    result = codewalker_convert.convert_xml_to_binary(xml_dir, out_dir, "TestPed01")

    assert result.status == "converted"
    produced_names = {Path(p).name for p in result.produced_files}
    assert produced_names == {"TestPed01.ytd", "TestPed01.ydd", "TestPed01.yft"}
    for name in produced_names:
        assert (out_dir / name).stat().st_size > 0


def test_real_cli_skips_ymt_without_corrupting_output(cli_env, tmp_path: Path):
    """Locks in the documented limitation: ymt_builder.py's schema doesn't
    match the real CPedVariationInfo PSO struct yet (point 10), so the CLI
    must refuse to convert it rather than emit wrong bytes.
    """
    xml_dir = tmp_path / "xml"
    out_dir = tmp_path / "out"
    xml_dir.mkdir()
    _write_minimal_cwxml(xml_dir, "TestPed01")

    result = codewalker_convert.convert_xml_to_binary(xml_dir, out_dir, "TestPed01")

    assert not (out_dir / "TestPed01.ymt").exists()
    assert "ymt" in result.message.lower()
