from pathlib import Path

from mcp_server import blender_runner
from scripts import package_builder, xml_manifest_updater as xmu


def test_blender_mock_fallback_produces_placeholders(tmp_path: Path):
    output_dir = tmp_path / "compiled"
    result = blender_runner.compile_ped(
        template_path=tmp_path / "missing_template.blend",
        source_fbx=tmp_path / "missing.fbx",
        output_dir=output_dir,
        model_name="Foo01",
    )
    assert result.mock is True
    assert result.status == "mock"
    assert len(result.produced_files) == 3
    for p in result.produced_files:
        assert p.exists()
        assert "[MOCK]" in p.read_text()


def test_end_to_end_mock_pipeline_builds_delivery_zip(tmp_path: Path):
    model_name = "BeachSurfer01"
    compiled_dir = tmp_path / "compiled"
    manifest_path = tmp_path / "ap_m.xml"

    compile_result = blender_runner.compile_ped(
        template_path=tmp_path / "missing_template.blend",
        source_fbx=tmp_path / "missing.fbx",
        output_dir=compiled_dir,
        model_name=model_name,
    )
    assert compile_result.mock is True

    xmu.add_ped_to_manifest(manifest_path, model_name, gender="female")

    zip_path = package_builder.build_delivery_zip(
        output_dir=tmp_path / "dist",
        model_name=model_name,
        compiled_dir=compiled_dir,
        manifest_path=manifest_path,
    )

    assert zip_path.exists()
    assert zip_path.stat().st_size > 0

    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any("UserMods/ap_m.xml" in n for n in names)
    assert any(f"dlcpack_standalone/{model_name}/content.xml" in n for n in names)
    assert any(f"dlcpack_standalone/{model_name}/setup2.xml" in n for n in names)
    assert any(model_name in n and n.endswith(".ydd.xml") for n in names)
