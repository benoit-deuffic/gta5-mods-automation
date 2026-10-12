"""Tests the orchestration logic in scripts/import_layered_ped.py WITHOUT a
real Blender (bpy mocked) -- locks down that build_layered_ped() wires the
three pieces proven separately this session (2026-10-12) in the right
order and with the right arguments: resolve_layered_components() ->
Sollumz import per resolved component -> rig_unskinned_body_components() ->
relink_external_textures(). Cannot validate the real Sollumz/Cycles
behavior itself (needs real Blender, see docs/architecture-decisions.md
point 9) -- that part was proven interactively against the real
peds/template_ped/ asset tree on bora.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "import_layered_ped.py"
SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")


class FakeObject:
    def __init__(self, obj_type="MESH"):
        self.type = obj_type
        self.hide_render = False
        self._hidden = False

    def hide_set(self, value):
        self._hidden = value


class FakeArmature(FakeObject):
    def __init__(self):
        super().__init__(obj_type="ARMATURE")
        self.name = "FakeArmature"
        self.children = []


@pytest.fixture
def fake_bpy_module(monkeypatch):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.ops = MagicMock()
    fake_bpy.context = MagicMock()
    fake_bpy.data = MagicMock()
    fake_bpy.data.objects = []
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    # blender_rig_export.py does `import mathutils` inside a function body
    # only (scale_to_reference_bbox) -- not touched by this module's import,
    # so no mathutils stub needed here (import_layered_ped.py's own
    # mathutils use is inside render_preview()/_frame_camera_on(), neither
    # of which build_layered_ped() calls).
    return fake_bpy


def _load_module(fake_bpy_module, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import importlib.util
    spec = importlib.util.spec_from_file_location("import_layered_ped", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_tier(tier_dir: Path, filenames):
    tier_dir.mkdir(parents=True, exist_ok=True)
    for fn in filenames:
        (tier_dir / fn).write_text("<Drawables/>", encoding="utf-8")


@pytest.fixture
def layered_fixture_tree(tmp_path):
    """A tiny synthetic 3-tier tree: base_all defines jbib+lowr, top
    overrides lowr -- mirrors the real convention exactly (see
    layered_ped_builder's module docstring)."""
    ped_name = "fake_ped"
    ped_root = tmp_path / "ped_root"
    top_dir = ped_root / ped_name
    mid_dir = top_dir / "base" / ped_name
    base_all_dir = top_dir / "base" / "base_all" / ped_name

    _make_tier(base_all_dir, ["jbib_000_u.ydd.xml", "lowr_002_r.ydd.xml"])
    _make_tier(mid_dir, [])
    _make_tier(top_dir, ["lowr_000_r.ydd.xml"])  # overrides lowr from base_all

    return ped_root, ped_name


def test_build_layered_ped_imports_each_resolved_component_once(
        fake_bpy_module, monkeypatch, layered_fixture_tree):
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree

    armature = FakeArmature()
    monkeypatch.setattr(module.bre, "find_reference_armature", lambda objs: armature)

    def fake_import(directory, files):
        fake_bpy_module.data.objects.append(FakeObject("MESH"))

    fake_bpy_module.ops.sollumz.import_assets.side_effect = fake_import

    rigged_calls = []
    monkeypatch.setattr(module.bre, "rig_unskinned_body_components",
                         lambda arm, meshes: rigged_calls.append((arm, list(meshes))) or meshes)

    relink_calls = []
    monkeypatch.setattr(module.bre, "relink_external_textures",
                         lambda root: relink_calls.append(root) or {"relinked": ["a"], "not_found": ["b"]})

    stats = module.build_layered_ped("fake_template.blend", str(ped_root), ped_name)

    # top tier's lowr file must win, not base_all's.
    assert Path(stats["resolved_components"]["lowr"]).name == "lowr_000_r.ydd.xml"
    assert Path(stats["resolved_components"]["jbib"]).name == "jbib_000_u.ydd.xml"

    fake_bpy_module.ops.wm.open_mainfile.assert_called_once_with(filepath="fake_template.blend")
    assert fake_bpy_module.ops.sollumz.import_assets.call_count == 2  # jbib + lowr
    assert stats["imported_mesh_count"] == 2
    assert stats["armature"] == "FakeArmature"

    assert len(rigged_calls) == 1
    assert rigged_calls[0][0] is armature
    assert len(rigged_calls[0][1]) == 2

    assert relink_calls == [str(ped_root)]
    assert stats["textures_relinked"] == 1
    assert stats["textures_not_found"] == ["b"]


def test_build_layered_ped_reports_missing_armature_without_crashing(
        fake_bpy_module, monkeypatch, layered_fixture_tree):
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree

    monkeypatch.setattr(module.bre, "find_reference_armature", lambda objs: None)

    stats = module.build_layered_ped("fake_template.blend", str(ped_root), ped_name)

    assert stats["armature"] is None
    assert "error" in stats
    # Must bail out before even attempting a Sollumz import.
    fake_bpy_module.ops.sollumz.import_assets.assert_not_called()


def test_build_layered_ped_records_failed_imports_without_crashing(
        fake_bpy_module, monkeypatch, layered_fixture_tree):
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree

    armature = FakeArmature()
    monkeypatch.setattr(module.bre, "find_reference_armature", lambda objs: armature)
    fake_bpy_module.ops.sollumz.import_assets.side_effect = RuntimeError("boom")
    monkeypatch.setattr(module.bre, "rig_unskinned_body_components", lambda arm, meshes: [])
    monkeypatch.setattr(module.bre, "relink_external_textures",
                         lambda root: {"relinked": [], "not_found": []})

    stats = module.build_layered_ped("fake_template.blend", str(ped_root), ped_name)

    assert len(stats["failed_imports"]) == 2  # both jbib and lowr failed
    assert all("boom" in msg for _comp, msg in stats["failed_imports"])
    assert stats["imported_mesh_count"] == 0
