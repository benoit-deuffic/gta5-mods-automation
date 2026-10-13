"""ped_package: replaying a fitted Deformation on CodeWalker-XML .ydd data.

The real chain (fit in Blender -> --deformation-out -> ped_package ->
codewalker-cli) was verified on CatalinaNude 80A/round: the shipped High LOD
matches the Blender mesh to 1e-4 mm, re-imported glute ratios are identical
(0.701/0.669), embedded .dds byte-identical. These tests pin the pure logic.
"""
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from body_morphology import Deformation, Lobe, excess_scale, region_weight  # noqa: E402
from ped_package import PackageError, package_ped, reshape_ped_xml  # noqa: E402


def deform_drawable_dictionary(root, deformations, skeleton, component="uppr"):
    return reshape_ped_xml({component: root}, deformations, skeleton)[component]

# Full effect at every angle and every z in [-1, 1]: excess over a 0.1 m
# wall is doubled.
_EVERYWHERE = Lobe(0.0, (180.0, 181.0), -1.0, -1.0, 1.0, 2.0)
_DOUBLE = Deformation(("SPR_",), (0.0, 0.0), (0.1,) * 8, ((_EVERYWHERE, 2.0),))
_SKELETON = ["SKEL_ROOT", "FB_Brow_000", "SPR_L_Breast", "SKEL_L_Hand"]

_YDD = """<?xml version="1.0" encoding="UTF-8"?>
<DrawableDictionary>
 <Item>
  <Name>uppr_000_r</Name>
  <BoundingSphereCenter x="0" y="0" z="1" />
  <BoundingSphereRadius value="1" />
  <BoundingBoxMin x="-0.2" y="-0.2" z="0.5" />
  <BoundingBoxMax x="0.2" y="0.2" z="1.5" />
  <DrawableModelsHigh>
   <Item>
    <Unknown1 value="{declared}" />
    <Geometries>
     <Item>
      <BoundingBoxMin x="-0.2" y="-0.2" z="-0.5" w="0" />
      <BoundingBoxMax x="0.2" y="0.2" z="0.5" w="0" />
      <BoneIDs>{bone_ids}</BoneIDs>
      <VertexBuffer>
       <Layout type="GTAV1">
        <Position />
        <BlendWeights />
        <BlendIndices />
        <Normal />
        <TexCoord0 />
        <Tangent />
       </Layout>
       <Data2>
        0.2 0 0   255 0 0 0   {breast} 0 0 0   1 0 0   0.5 0.5   0 1 0 1
        -0.2 0 0   255 0 0 0   {hand} 0 0 0   -1 0 0   0.5 0.5   0 1 0 1
       </Data2>
      </VertexBuffer>
     </Item>
    </Geometries>
   </Item>
  </DrawableModelsHigh>
 </Item>
</DrawableDictionary>
"""


def _ydd(declared=4, bone_ids="0, 1, 2, 3", breast=2, hand=3):
    return ET.fromstring(_YDD.format(declared=declared, bone_ids=bone_ids, breast=breast, hand=hand))


def _vertices(root):
    return [line.split() for line in root.find(".//Data2").text.split("\n") if line.strip()]


def test_deformation_round_trips_through_json_and_matches_excess_scale():
    shifted = Deformation(("SPR_",), (0.0, 0.0), (0.1,) * 8, ((_EVERYWHERE, 2.0),), (_EVERYWHERE, -1.0, 2.0, 0.01))
    contracted = Deformation(("SPR_",), (0.0, 0.0), (0.1,) * 8, ((_EVERYWHERE, 0.5),),
                             contract=(_EVERYWHERE, 0.33, 0.5, 0.4), upper_wall=(0.26, 0.46, (0.08,) * 8))
    for d in (_DOUBLE, shifted, contracted):
        assert Deformation.from_dict(json.loads(json.dumps(d.to_dict()))) == d
    pts = [(0.2, 0.0, 0.0), (0.05, 0.0, 0.0)]
    assert _DOUBLE.apply(pts, [1.0, 1.0]) == excess_scale(pts, [1.0, 1.0], (0.0, 0.0), (0.1,) * 8,
                                                          [(_EVERYWHERE, 2.0)])


def test_region_weight_sums_matching_bones_capped_at_one():
    assert region_weight([("SPR_L", 0.6), ("SKEL_Hand", 0.4)], ("SPR_",)) == 0.6
    assert region_weight([("SPR_L", 0.7), ("SPR_R", 0.7)], ("SPR_",)) == 1.0


def test_only_weighted_vertices_move_and_their_normal_follows():
    root = _ydd()
    untouched_line = [line for line in root.find(".//Data2").text.split("\n") if line.strip()][1]
    assert deform_drawable_dictionary(root, [_DOUBLE], _SKELETON) == 1
    breast, hand = _vertices(root)
    assert [float(c) for c in breast[:3]] == pytest.approx([0.3, 0.0, 0.0])  # excess 0.1 -> 0.2
    # radial stretch keeps a radial normal (finite-difference Jacobian: ~1e-4)
    assert [float(c) for c in breast[11:14]] == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)
    assert math.dist([float(c) for c in breast[16:19]], (0, 0, 0)) == pytest.approx(1.0)
    assert breast[19] == "1"  # tangent handedness kept
    assert [line for line in root.find(".//Data2").text.split("\n") if line.strip()][1] == untouched_line
    assert hand[:3] == ["-0.2", "0", "0"]


def test_bounds_grow_by_the_geometry_growth_only():
    root = _ydd()
    deform_drawable_dictionary(root, [_DOUBLE], _SKELETON)
    assert float(root.find(".//Geometries/Item/BoundingBoxMax").get("x")) == pytest.approx(0.3)
    # the drawable box is in another frame (z 0.5..1.5): shifted by the
    # growth (0.1 in x), not unioned with geometry coordinates
    drawable_max = root.find("Item/BoundingBoxMax")
    assert float(drawable_max.get("x")) == pytest.approx(0.3)
    assert float(drawable_max.get("z")) == pytest.approx(1.5)
    assert float(root.find("Item/BoundingBoxMin").get("z")) == pytest.approx(0.5)
    assert float(root.find("Item/BoundingSphereRadius").get("value")) == pytest.approx(1.1)


def test_nothing_weighted_leaves_the_file_untouched():
    root = _ydd(breast=3)  # both vertices on the hand
    before = ET.tostring(root)
    assert deform_drawable_dictionary(root, [_DOUBLE], _SKELETON) == 0
    assert ET.tostring(root) == before


def test_palette_built_without_facial_bones_is_remapped_like_the_blender_import():
    # 3-slot palette = skeleton minus FB_*_000: slot 1 is SPR_L_Breast, not
    # the facial bone at skeleton index 1
    root = _ydd(declared=3, bone_ids="0, 1, 2", breast=1, hand=2)
    assert deform_drawable_dictionary(root, [_DOUBLE], _SKELETON) == 1


def test_bone_ids_map_palette_slots_to_skeleton_bones():
    root = _ydd(bone_ids="3, 2", breast=1, hand=0)  # slot 1 -> bone 2 (SPR)
    assert deform_drawable_dictionary(root, [_DOUBLE], _SKELETON) == 1


def _spec(tmp_path, components, ped_root=None):
    spec = tmp_path / "x.deform.json"
    spec.write_text(json.dumps({
        "ped_root": str(ped_root or tmp_path / "xml"), "ped_name": "Ped",
        "skeleton_yft": str(_skeleton(tmp_path)), "components": components,
        "deformations": [_DOUBLE.to_dict()]}))
    return spec


def _skeleton(tmp_path):
    yft = tmp_path / "Ped.yft.xml"
    yft.write_text("<Fragment><Drawable><Skeleton><Bones>"
                   + "".join(f"<Item><Name>{n}</Name></Item>" for n in _SKELETON)
                   + "</Bones></Skeleton></Drawable></Fragment>")
    return yft


def _source(tmp_path, with_ymt=True):
    src = tmp_path / "src"
    (src / "Ped").mkdir(parents=True, exist_ok=True)
    (src / "Ped.yft").write_bytes(b"yft")
    (src / "Ped.ymt").write_bytes(b"ymt")
    if not with_ymt:
        (src / "Ped.ymt").unlink()
    return src


def test_package_refuses_bad_name_missing_original_and_existing_output(tmp_path):
    spec = _spec(tmp_path, {})
    with pytest.raises(PackageError, match="invalid ped name"):
        package_ped(spec, _source(tmp_path), "../evil", tmp_path / "out")
    with pytest.raises(PackageError, match="Ped.ymt"):
        package_ped(spec, _source(tmp_path, with_ymt=False), "New", tmp_path / "out")
    (tmp_path / "out" / "New").mkdir(parents=True)
    with pytest.raises(PackageError, match="not overwriting"):
        package_ped(spec, _source(tmp_path), "New", tmp_path / "out")


def test_package_refuses_a_reshaped_component_from_another_tier(tmp_path):
    lower = tmp_path / "xml" / "base" / "Ped"
    lower.mkdir(parents=True)
    (lower / "uppr_000_r.ydd.xml").write_text(_YDD.format(declared=4, bone_ids="0, 1, 2, 3", breast=2, hand=3))
    spec = _spec(tmp_path, {"uppr": str(lower / "uppr_000_r.ydd.xml")})
    with pytest.raises(PackageError, match="isn't one of Ped's own files"):
        package_ped(spec, _source(tmp_path), "New", tmp_path / "out")
    assert not (tmp_path / "out" / "New").exists()


def test_package_copies_originals_renamed_when_nothing_is_reshaped(tmp_path):
    src = _source(tmp_path)
    (src / "Ped" / "head_000_r.ydd").write_bytes(b"head")
    spec = tmp_path / "x.deform.json"
    spec.write_text(json.dumps({"ped_root": str(tmp_path / "xml"), "ped_name": "Ped", "skeleton_yft": None,
                                "components": {}, "deformations": []}))
    report = package_ped(spec, src, "New", tmp_path / "out")
    assert report["reshaped"] == {}
    assert (tmp_path / "out" / "New.yft").read_bytes() == b"yft"
    assert (tmp_path / "out" / "New.ymt").read_bytes() == b"ymt"
    assert (tmp_path / "out" / "New" / "head_000_r.ydd").read_bytes() == b"head"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["New", "New.yft", "New.ymt"]


def test_non_skin_components_follow_the_skin_instead_of_being_reshaped():
    skin = _ydd()
    # a "necklace" 1 cm in front of the moving breast vertex, itself skinned
    # to the breast bone: reshaped on its own it would move 2 cm (its excess
    # 0.11 doubled); following the skin it moves like the skin vertex (+0.1)
    necklace = ET.fromstring(_YDD.format(declared=4, bone_ids="0, 1, 2, 3", breast=2, hand=2)
                             .replace("0.2 0 0   255", "0.21 0 0   255", 1)
                             .replace("-0.2 0 0   255", "-0.5 0 0   255", 1))
    moved = reshape_ped_xml({"uppr": skin, "accs": necklace}, [_DOUBLE], _SKELETON)
    assert moved == {"uppr": 1, "accs": 1}
    near, far = _vertices(necklace)
    assert [float(c) for c in near[:3]] == pytest.approx([0.31, 0.0, 0.0])
    assert near[11:14] == ["1", "0", "0"]  # translated: normal untouched
    assert far[:3] == ["-0.5", "0", "0"]   # no skin vertex within reach
