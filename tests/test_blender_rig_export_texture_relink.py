"""Tests scripts/blender_rig_export.py's relink_external_textures() WITHOUT a
real Blender (bpy is mocked) -- same pattern as the other blender_rig_export
test files. Locks down a real bug found 2026-10-12 while rendering a layered
MP Freemode ped import: Sollumz's CodeWalker-XML import leaves every Image
datablock pointing at a RELATIVE path ("//name.dds") resolved against the
.blend file's own directory, which never matches where the real .dds
actually sits (next to the exported .ydd.xml, a completely different
directory tree) -- the fix walks a given search root for .dds files and
relinks by basename. See docs/architecture-decisions.md for the full story
and why `has_data` is NOT a reliable signal of relink success.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "blender_rig_export.py"


@pytest.fixture
def fake_bpy_module(monkeypatch):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.ops = MagicMock()
    fake_bpy.context = MagicMock()
    fake_bpy.data = MagicMock()
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    return fake_bpy


def _load_module(fake_bpy_module):
    import importlib.util
    spec = importlib.util.spec_from_file_location("blender_rig_export", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeImage:
    def __init__(self, name, source='FILE', filepath=""):
        self.name = name
        self.source = source
        self.filepath = filepath
        self.reload = MagicMock()


def test_relink_finds_and_relinks_matching_dds_by_basename(fake_bpy_module, tmp_path):
    module = _load_module(fake_bpy_module)

    # Real file on disk, nested (mimics the real export tree's subfolders).
    nested = tmp_path / "some_dlc" / "subfolder"
    nested.mkdir(parents=True)
    real_dds = nested / "jbib_diff_000_a_uni.dds"
    real_dds.write_bytes(b"fake dds bytes")

    img = FakeImage("jbib_diff_000_a_uni", source='FILE', filepath="//jbib_diff_000_a_uni.dds")
    fake_bpy_module.data.images = [img]

    result = module.relink_external_textures(str(tmp_path))

    assert result == {"relinked": ["jbib_diff_000_a_uni"], "not_found": []}
    assert img.filepath == str(real_dds)
    img.reload.assert_called_once()


def test_relink_is_case_insensitive_on_basename(fake_bpy_module, tmp_path):
    module = _load_module(fake_bpy_module)
    real_dds = tmp_path / "Hair_Diff_000_A_Uni.dds"
    real_dds.write_bytes(b"x")

    img = FakeImage("hair_diff_000_a_uni")
    fake_bpy_module.data.images = [img]

    result = module.relink_external_textures(str(tmp_path))

    assert result["relinked"] == ["hair_diff_000_a_uni"]
    assert img.filepath == str(real_dds)


def test_relink_reports_not_found_without_crashing(fake_bpy_module, tmp_path):
    module = _load_module(fake_bpy_module)
    img = FakeImage("totally_missing_texture", filepath="//totally_missing_texture.dds")
    fake_bpy_module.data.images = [img]

    result = module.relink_external_textures(str(tmp_path))

    assert result == {"relinked": [], "not_found": ["totally_missing_texture"]}
    # Must not have touched the image at all.
    assert img.filepath == "//totally_missing_texture.dds"
    img.reload.assert_not_called()


def test_relink_skips_non_file_backed_images(fake_bpy_module, tmp_path):
    module = _load_module(fake_bpy_module)
    real_dds = tmp_path / "generated_texture.dds"
    real_dds.write_bytes(b"x")

    img = FakeImage("generated_texture", source='GENERATED')
    fake_bpy_module.data.images = [img]

    result = module.relink_external_textures(str(tmp_path))

    assert result == {"relinked": [], "not_found": []}
    img.reload.assert_not_called()


def test_relink_picks_first_match_when_duplicate_basenames_exist(fake_bpy_module, tmp_path):
    # Real datasets (base_all/mid/top tiers) can have same-named files in
    # different subfolders -- relink must not crash, just pick one
    # deterministically (first one encountered by os.walk).
    module = _load_module(fake_bpy_module)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "dup.dds").write_bytes(b"x")
    (tmp_path / "b" / "dup.dds").write_bytes(b"x")

    img = FakeImage("dup")
    fake_bpy_module.data.images = [img]

    result = module.relink_external_textures(str(tmp_path))

    assert result["relinked"] == ["dup"]
    assert img.filepath.endswith("dup.dds")
