from pathlib import Path

from mcp_server import validator


def test_validate_fbx_missing_file(tmp_path: Path):
    report = validator.validate_fbx_file(tmp_path / "nope.fbx")
    assert not report.ok
    assert any("not found" in e for e in report.errors)


def test_validate_fbx_too_small(tmp_path: Path):
    f = tmp_path / "tiny.fbx"
    f.write_bytes(b"Kaydara FBX Binary  \x00")
    report = validator.validate_fbx_file(f, min_bytes=1000)
    assert not report.ok


def test_validate_fbx_ok(tmp_path: Path):
    f = tmp_path / "ok.fbx"
    f.write_bytes(b"Kaydara FBX Binary  " + b"0" * 2000)
    report = validator.validate_fbx_file(f, min_bytes=1000)
    assert report.ok


def test_validate_manifest_missing(tmp_path: Path):
    report = validator.validate_manifest_xml(tmp_path / "ap_m.xml", "Foo")
    assert not report.ok


def test_validate_manifest_name_present(tmp_path: Path):
    from scripts import xml_manifest_updater as xmu
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "Foo01", gender="female")
    report = validator.validate_manifest_xml(manifest, "Foo01")
    assert report.ok


def test_validate_compiled_artifacts_mock_warns_not_errors(tmp_path: Path):
    for suffix in (".ydd.xml", ".yft.xml", ".ytd.xml", ".ymt.xml"):
        (tmp_path / f"Foo01{suffix}").write_text("<!-- mock -->")
    report = validator.validate_compiled_artifacts(tmp_path, "Foo01")
    assert report.ok
    assert report.warnings


def test_validate_compiled_artifacts_missing_errors(tmp_path: Path):
    report = validator.validate_compiled_artifacts(tmp_path, "Foo01", allow_mock_placeholders=False)
    assert not report.ok
    assert len(report.errors) == 4
