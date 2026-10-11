"""Integration test for scripts/codewalker_convert.py against the REAL
CodeWalker-Core-based CLI (tools/CodeWalkerCli), not a mock.

Skipped automatically when the CLI hasn't been built (CODEWALKER_CLI_PATH
unset/missing and no `codewalker-cli` on PATH) -- e.g. on a fresh checkout
before running scripts/fetch_codewalker.sh + `dotnet publish`. Verified to
pass for real: .ytd/.ydd/.yft on 2026-10-11 (point 10), .ymt added after
scripts/ymt_builder.py was rewritten against the real CPedVariationInfo PSO
struct and round-trip-verified via `codewalker-cli inspect` (point 11).
"""
import os
import shutil
import subprocess
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


def test_real_cli_converts_all_four_formats_to_binary(cli_env, tmp_path: Path):
    xml_dir = tmp_path / "xml"
    out_dir = tmp_path / "out"
    xml_dir.mkdir()
    _write_minimal_cwxml(xml_dir, "TestPed01")

    result = codewalker_convert.convert_xml_to_binary(xml_dir, out_dir, "TestPed01")

    assert result.status == "converted"
    produced_names = {Path(p).name for p in result.produced_files}
    assert produced_names == {"TestPed01.ytd", "TestPed01.ydd", "TestPed01.yft", "TestPed01.ymt"}
    for name in produced_names:
        assert (out_dir / name).stat().st_size > 0


def test_real_ymt_round_trips_through_codewalker_inspect(cli_env, tmp_path: Path):
    """Doesn't just check the conversion didn't throw: loads the produced
    .ymt back through CodeWalker's own YmtFile.Load() (via `codewalker-cli
    inspect`) and checks it recognizes CPedVariationInfo with the exact
    112-byte struct size from MetaTypes.cs -- see
    docs/architecture-decisions.md point 11.
    """
    xml_dir = tmp_path / "xml"
    out_dir = tmp_path / "out"
    xml_dir.mkdir()
    ymt_builder.write_ymt_xml(xml_dir / "TestPed01.ymt.xml", "TestPed01",
                              present_components=("head", "uppr", "lowr", "hand", "feet"))

    result = codewalker_convert.convert_xml_to_binary(xml_dir, out_dir, "TestPed01",
                                                       extensions=(".ymt",))
    assert result.status == "converted"
    ymt_path = out_dir / "TestPed01.ymt"
    assert ymt_path.exists()

    proc = subprocess.run([CLI_PATH, "inspect", str(ymt_path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "CPedVariationInfo, DataLength=112" in proc.stdout
    assert "CPVComponentData" in proc.stdout
    assert "CPVDrawblData" in proc.stdout


def test_real_cli_round_trip_fails_loudly_on_garbage_ymt(cli_env, tmp_path: Path):
    """Sanity check on the sanity check: inspect must actually detect a
    broken/empty file, not just always report success."""
    garbage = tmp_path / "garbage.ymt"
    garbage.write_bytes(b"not a real ymt file at all")

    proc = subprocess.run([CLI_PATH, "inspect", str(garbage)], capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
