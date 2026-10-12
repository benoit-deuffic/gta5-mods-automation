"""Tests the body-component classification/rigging helpers in
scripts/blender_rig_export.py WITHOUT a real Blender (bpy is mocked) --
same pattern as test_blender_rig_export_glb_routing.py. These lock down the
pure logic (which mesh is which component, which variant is "default",
which meshes need auto-rigging) found 2026-10-11 while debugging why a
real ped's torso/legs render was invisible -- see
docs/architecture-decisions.md and the gtav-ped-modding skill.
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


class FakeMaterial:
    def __init__(self, name):
        self.name = name


class FakeMaterialSlot:
    def __init__(self, material):
        self.material = material


class FakeVertexGroup:
    def __init__(self, name, index):
        self.name = name
        self.index = index


class FakeBone:
    def __init__(self, name):
        self.name = name


class FakeArmatureData:
    def __init__(self, bone_names):
        self.bones = [FakeBone(n) for n in bone_names]


class FakeMeshData:
    def __init__(self, n_vertices=10):
        self.vertices = list(range(n_vertices))


class FakeObject:
    def __init__(self, name, obj_type="MESH", material_names=(), vgroup_names=(),
                 n_vertices=10, bone_names=None):
        self.name = name
        self.type = obj_type
        self.material_slots = [FakeMaterialSlot(FakeMaterial(n)) for n in material_names]
        self.vertex_groups = [FakeVertexGroup(n, i) for i, n in enumerate(vgroup_names)]
        self.data = FakeArmatureData(bone_names) if obj_type == "ARMATURE" else FakeMeshData(n_vertices)
        self.select_set = MagicMock()


def test_classify_body_component_matches_real_naming_convention(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    obj = FakeObject("hash_57AFF7FB.model", material_names=["uppr_diff_000_a_whi"])
    assert module.classify_body_component(obj) == ("uppr", 0)


def test_classify_body_component_case_insensitive(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    obj = FakeObject("x", material_names=["UPPR_DIFF_002_a_whi"])
    assert module.classify_body_component(obj) == ("uppr", 2)


def test_classify_body_component_returns_none_for_unrecognized_material(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    obj = FakeObject("x", material_names=["some_random_material"])
    assert module.classify_body_component(obj) is None


def test_select_default_body_components_picks_lowest_variant(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    uppr_default = FakeObject("uppr_000", material_names=["uppr_diff_000_a_whi"])
    uppr_alt = FakeObject("uppr_001", material_names=["uppr_diff_001_a_whi"])
    lowr_default = FakeObject("lowr_000", material_names=["lowr_diff_000_a_whi"])
    not_a_component = FakeObject("some_prop", material_names=["random_mat"])

    selected, others = module.select_default_body_components(
        [uppr_default, uppr_alt, lowr_default, not_a_component])

    assert selected == {"uppr": uppr_default, "lowr": lowr_default}
    assert others == [uppr_alt]


def test_select_default_body_components_empty_when_no_components(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    selected, others = module.select_default_body_components([FakeObject("x", material_names=["nope"])])
    assert selected == {}
    assert others == []


def test_rig_unskinned_body_components_rigs_only_unskinned_meshes(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    armature = FakeObject("skel", obj_type="ARMATURE", bone_names=["SKEL_Head", "SKEL_Spine1"])
    already_skinned = FakeObject("head_mesh", vgroup_names=["SKEL_Head"])
    unskinned_uppr = FakeObject("uppr_mesh", vgroup_names=[])
    unskinned_empty_mesh = FakeObject("degenerate", vgroup_names=[], n_vertices=0)

    rigged = module.rig_unskinned_body_components(
        armature, [already_skinned, unskinned_uppr, unskinned_empty_mesh])

    assert rigged == [unskinned_uppr]
    unskinned_uppr.select_set.assert_called_once_with(True)
    already_skinned.select_set.assert_not_called()
    fake_bpy_module.ops.object.parent_set.assert_called_once_with(type='ARMATURE_AUTO')


def test_rig_unskinned_body_components_noop_when_nothing_to_rig(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    armature = FakeObject("skel", obj_type="ARMATURE", bone_names=["SKEL_Head"])
    already_skinned = FakeObject("head_mesh", vgroup_names=["SKEL_Head"])

    rigged = module.rig_unskinned_body_components(armature, [already_skinned])

    assert rigged == []
    fake_bpy_module.ops.object.parent_set.assert_not_called()


def test_meshes_without_weights_flags_empty_groups_not_just_missing_ones(fake_bpy_module):
    # A failed heat auto-weighting leaves every vertex group present but empty.
    module = _load_module(fake_bpy_module)
    from types import SimpleNamespace as NS
    weighted = NS(name="weighted", type="MESH", data=NS(vertices=[NS(groups=[NS(weight=0.7)])]))
    empty = NS(name="empty", type="MESH", data=NS(vertices=[NS(groups=[NS(weight=0.0)]), NS(groups=[])]))
    no_verts = NS(name="no_verts", type="MESH", data=NS(vertices=[]))
    assert module.meshes_without_weights([weighted, empty, no_verts]) == ["empty"]


def test_bind_meshes_to_armature_retargets_only_foreign_armature_modifiers(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    from types import SimpleNamespace as NS

    class Matrix:
        def copy(self):
            return self
    working, foreign = NS(name="working"), NS(name="foreign")
    skinned = NS(modifiers=[NS(type="ARMATURE", object=foreign)], parent=foreign, matrix_world=Matrix())
    unskinned = NS(modifiers=[], parent=None, matrix_world=Matrix())

    module.bind_meshes_to_armature(working, [skinned, unskinned])

    assert skinned.modifiers[0].object is working and skinned.parent is working
    assert unskinned.parent is None
