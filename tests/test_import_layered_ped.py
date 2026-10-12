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
    monkeypatch.setattr(module.bre, "bind_meshes_to_armature", lambda arm, meshes: None)
    monkeypatch.setattr(module.bre, "meshes_without_weights", lambda meshes: [])

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


def test_build_layered_ped_imports_with_ped_yft_as_external_skeleton(
        fake_bpy_module, monkeypatch, layered_fixture_tree):
    # The .ydd components are natively skinned but skeleton-less: the ped's
    # own <ped>.yft.xml next to the <ped>/ folder must be handed to Sollumz.
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree
    (ped_root / f"{ped_name}.yft.xml").write_text("<Fragment/>", encoding="utf-8")

    armature = FakeArmature()
    monkeypatch.setattr(module.bre, "find_reference_armature", lambda objs: armature)
    monkeypatch.setattr(module, "_external_skeleton_import_kwargs",
                         lambda yft: {"dwd_import_external_skeleton": "SAVED",
                                      "dwd_import_external_skeleton_saved_path": str(yft)})
    bound = []
    monkeypatch.setattr(module.bre, "bind_meshes_to_armature", lambda arm, meshes: bound.append(arm))
    monkeypatch.setattr(module.bre, "rig_unskinned_body_components", lambda arm, meshes: [])
    monkeypatch.setattr(module.bre, "meshes_without_weights", lambda meshes: [])
    monkeypatch.setattr(module.bre, "relink_external_textures", lambda root: {"relinked": [], "not_found": []})

    stats = module.build_layered_ped("fake_template.blend", str(ped_root), ped_name)

    for call in fake_bpy_module.ops.sollumz.import_assets.call_args_list:
        assert call.kwargs["dwd_import_external_skeleton"] == "SAVED"
        assert call.kwargs["dwd_import_external_skeleton_saved_path"].endswith(f"{ped_name}.yft.xml")
    assert stats["skeleton_yft"].endswith(f"{ped_name}.yft.xml")
    assert not stats["skeleton_is_fallback"]
    assert bound == [armature]


SKELETON_6 = ("<Fragment><Drawable><Skeleton><Bones>"
              + "".join(f"<Item><Name>{n}</Name></Item>"
                        for n in ("SKEL_ROOT", "A", "FB_X_000", "FB_Y_000", "B", "C"))
              + "</Bones></Skeleton></Drawable></Fragment>")


def _stub_rigging(module, monkeypatch):
    monkeypatch.setattr(module.bre, "find_reference_armature", lambda objs: FakeArmature())
    monkeypatch.setattr(module, "_external_skeleton_import_kwargs",
                         lambda yft: {"dwd_import_external_skeleton_saved_path": str(yft)})
    monkeypatch.setattr(module.bre, "bind_meshes_to_armature", lambda arm, meshes: None)
    monkeypatch.setattr(module.bre, "rig_unskinned_body_components", lambda arm, meshes: [])
    monkeypatch.setattr(module.bre, "meshes_without_weights", lambda meshes: [])
    monkeypatch.setattr(module.bre, "relink_external_textures", lambda root: {"relinked": [], "not_found": []})


def test_ped_without_own_yft_uses_fallback_skeleton_and_fixes_bone_indices(
        fake_bpy_module, monkeypatch, layered_fixture_tree, tmp_path):
    # An MP Freemode component pack ships no .yft; its components were built
    # for a smaller vanilla skeleton than the template's (here 4 vs 6 bones
    # with 2 inserted FB_*_000): weights on the inserted bones' indices must
    # move to the bones the component actually meant.
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree
    (ped_root / ped_name / "lowr_000_r.ydd.xml").write_text('<Unknown1 value="4" />', encoding="utf-8")
    skeleton = tmp_path / "template_skeleton.yft.xml"
    skeleton.write_text(SKELETON_6, encoding="utf-8")
    _stub_rigging(module, monkeypatch)
    remaps = []
    monkeypatch.setattr(module.bre, "remap_vertex_groups", lambda meshes, mapping: remaps.append(mapping) or 7)

    stats = module.build_layered_ped("t.blend", str(ped_root), ped_name, fallback_skeleton_yft=str(skeleton))

    for call in fake_bpy_module.ops.sollumz.import_assets.call_args_list:
        assert call.kwargs["dwd_import_external_skeleton_saved_path"] == str(skeleton)
    assert stats["skeleton_yft"] == str(skeleton) and stats["skeleton_is_fallback"]
    assert remaps == [{"FB_X_000": "B", "FB_Y_000": "C"}]  # only lowr declares a bone count
    assert stats["bone_remaps"] == {"lowr": 7} and stats["bone_remap_errors"] == {}


def test_unknown_component_skeleton_layout_is_reported_not_guessed(
        fake_bpy_module, monkeypatch, layered_fixture_tree, tmp_path):
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree
    (ped_root / ped_name / "lowr_000_r.ydd.xml").write_text('<Unknown1 value="3" />', encoding="utf-8")
    skeleton = tmp_path / "s.yft.xml"
    skeleton.write_text(SKELETON_6, encoding="utf-8")
    _stub_rigging(module, monkeypatch)
    monkeypatch.setattr(module.bre, "remap_vertex_groups", lambda meshes, mapping: pytest.fail("guessed"))

    stats = module.build_layered_ped("t.blend", str(ped_root), ped_name, fallback_skeleton_yft=str(skeleton))

    assert "unknown bone layout" in stats["bone_remap_errors"]["lowr"]


def test_missing_fallback_skeleton_fails_before_import(fake_bpy_module, monkeypatch, layered_fixture_tree, tmp_path):
    module = _load_module(fake_bpy_module, monkeypatch)
    ped_root, ped_name = layered_fixture_tree
    _stub_rigging(module, monkeypatch)

    stats = module.build_layered_ped("t.blend", str(ped_root), ped_name,
                                     fallback_skeleton_yft=str(tmp_path / "nope.yft.xml"))

    assert "fallback skeleton not found" in stats["error"]
    assert not fake_bpy_module.ops.sollumz.import_assets.called
