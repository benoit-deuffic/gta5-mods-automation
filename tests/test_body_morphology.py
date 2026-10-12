"""Tests build_pose_bone_scale_map() in scripts/body_morphology.py -- the
pure-logic half of the bone-scale body morphology system (no bpy needed).
apply_body_morphology() itself (the bpy-dependent half) was verified for
real against templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend on
2026-10-12 -- see docs/architecture-decisions.md.
"""
from scripts.body_morphology import (
    MORPH_SLIDERS, SLIDER_MIN, SLIDER_MAX, build_pose_bone_scale_map,
)


def test_single_slider_maps_to_its_bones_full_scale():
    result = build_pose_bone_scale_map({"shoulder_width": 1.2})
    assert result == {
        "SKEL_L_Clavicle": (1.2, 1.2, 1.2),
        "SKEL_R_Clavicle": (1.2, 1.2, 1.2),
    }


def test_axis_masked_slider_only_scales_its_own_axis():
    # bust_size only touches local Z -- X/Y must stay at 1.0 (no-op), this
    # is the whole point of the per-axis precision added 2026-10-12.
    result = build_pose_bone_scale_map({"bust_size": 1.3})
    assert result["SPR_L_Breast"] == (1.0, 1.0, 1.3)
    assert result["SPR_R_Breast"] == (1.0, 1.0, 1.3)


def test_bust_width_projection_size_are_independent_axes():
    result = build_pose_bone_scale_map({
        "bust_width": 1.1, "bust_projection": 1.2, "bust_size": 1.3,
    })
    assert result["SPR_L_Breast"] == (1.1, 1.2, 1.3)


def test_hip_width_and_hip_depth_target_different_pelvis_axes():
    result = build_pose_bone_scale_map({"hip_width": 1.2, "hip_depth": 1.1})
    assert result["SKEL_Pelvis"] == (1.0, 1.1, 1.2)


def test_unknown_slider_is_skipped_without_crashing():
    result = build_pose_bone_scale_map({"totally_made_up_slider": 2.0, "height": 1.1})
    assert result == {"SKEL_ROOT": (1.1, 1.1, 1.1)}


def test_value_clamped_to_slider_max():
    result = build_pose_bone_scale_map({"height": 5.0})
    assert result == {"SKEL_ROOT": (SLIDER_MAX, SLIDER_MAX, SLIDER_MAX)}


def test_value_clamped_to_slider_min():
    result = build_pose_bone_scale_map({"height": -3.0})
    assert result == {"SKEL_ROOT": (SLIDER_MIN, SLIDER_MIN, SLIDER_MIN)}


def test_default_unchanged_value_is_one():
    result = build_pose_bone_scale_map({"height": 1.0})
    assert result == {"SKEL_ROOT": (1.0, 1.0, 1.0)}


def test_multiple_independent_sliders_combine():
    result = build_pose_bone_scale_map({"height": 1.1, "leg_length": 0.9})
    assert result["SKEL_ROOT"] == (1.1, 1.1, 1.1)
    for bone in ("SKEL_L_Thigh", "SKEL_R_Thigh", "SKEL_L_Calf", "SKEL_R_Calf"):
        assert result[bone] == (0.9, 0.9, 0.9)


def test_overlapping_sliders_on_same_bone_axis_multiply_not_overwrite():
    # Construct a synthetic overlap by calling the map-building logic twice
    # with sliders that happen to share a bone+axis target, to lock down the
    # documented "multiply, don't overwrite" behavior even though no two
    # CURRENT entries in MORPH_SLIDERS actually overlap yet.
    import scripts.body_morphology as module
    try:
        module.MORPH_SLIDERS["_test_overlap_a"] = {"SKEL_Spine1": (1, 1, 1)}
        module.MORPH_SLIDERS["_test_overlap_b"] = {"SKEL_Spine1": (1, 1, 1)}
        result = module.build_pose_bone_scale_map({"_test_overlap_a": 1.2, "_test_overlap_b": 1.1})
        expected = 1.2 * 1.1
        assert result["SKEL_Spine1"] == (expected, expected, expected)
    finally:
        del module.MORPH_SLIDERS["_test_overlap_a"]
        del module.MORPH_SLIDERS["_test_overlap_b"]


def test_empty_input_produces_empty_map():
    assert build_pose_bone_scale_map({}) == {}


def test_all_documented_sliders_are_non_empty_bone_axis_maps():
    # Guards against a typo leaving a slider with an empty/missing bone map,
    # or an axis mask with the wrong shape/out-of-range values.
    for slider_name, bone_axis_masks in MORPH_SLIDERS.items():
        assert isinstance(bone_axis_masks, dict) and len(bone_axis_masks) > 0, slider_name
        for bone, mask in bone_axis_masks.items():
            assert isinstance(bone, str) and bone.startswith(("SKEL_", "SPR_")), (slider_name, bone)
            assert len(mask) == 3 and all(v in (0, 1) for v in mask), (slider_name, bone, mask)


def test_parse_slider_spec_reads_name_value_pairs():
    from scripts.body_morphology import parse_slider_spec
    assert parse_slider_spec("bust_size=1.2, hip_width=1.1,") == {"bust_size": 1.2, "hip_width": 1.1}


def test_parse_slider_spec_rejects_malformed_entry():
    import pytest
    from scripts.body_morphology import parse_slider_spec
    with pytest.raises(ValueError):
        parse_slider_spec("bust_size1.2")
    with pytest.raises(ValueError):
        parse_slider_spec("bust_size=big")


def test_regional_scale_fades_from_center_to_radius():
    from scripts.body_morphology import regional_scale_points
    axes = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    center = (0.0, 0.0, 0.0)
    # only projection (local y) shrinks
    points = [(0.0, 0.0, 0.0), (0.0, -0.05, 0.0), (0.0, -0.1, 0.0), (0.0, -0.2, 0.0), (0.05, 0.0, 0.0)]
    out = regional_scale_points(points, center, axes, (1.0, 0.5, 1.0), inner=0.0, outer=0.1)

    assert out[0] == (0.0, 0.0, 0.0)              # center fixed
    w = (1 - 0.25) ** 2                           # d/r = 0.5
    assert abs(out[1][1] - (-0.05 * (1 - 0.5 * w))) < 1e-12
    assert out[2] == (0.0, -0.1, 0.0)             # at radius: unchanged
    assert out[3] == (0.0, -0.2, 0.0)             # outside: unchanged
    assert abs(out[4][0] - 0.05) < 1e-12 and abs(out[4][1]) < 1e-12  # x axis untouched


def test_regional_scale_follows_rotated_bone_axes():
    from scripts.body_morphology import regional_scale_points
    # bone local x points along world y: scaling local x must move world y only
    axes = [(0, 1, 0), (-1, 0, 0), (0, 0, 1)]
    out = regional_scale_points([(0.0, 0.02, 0.0)], (0.0, 0.0, 0.0), axes, (2.0, 1.0, 1.0), inner=0.0, outer=1.0)
    w = (1 - 0.0004) ** 2
    assert abs(out[0][0]) < 1e-12
    assert abs(out[0][1] - 0.02 * (1 + w)) < 1e-12


def test_regional_scale_is_full_strength_inside_inner_radius():
    from scripts.body_morphology import regional_scale_points
    axes = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    out = regional_scale_points([(0.0, -0.05, 0.0)], (0.0, 0.0, 0.0), axes, (1.0, 0.8, 1.0), inner=0.06, outer=0.11)
    assert abs(out[0][1] - (-0.04)) < 1e-12


def test_bust_region_anchor_is_apex_pushed_back_to_sternum_depth():
    from scripts.body_morphology import bust_region_anchor
    forward = (0.0, -1.0, 0.0)  # GTA peds face -y
    points = [
        (0.10, -0.18, 0.34),   # left breast apex (most forward on that side)
        (0.10, -0.15, 0.28),   # lower breast
        (-0.10, -0.19, 0.34),  # right breast: other side, ignored for the left bone
        (0.0, -0.135, 0.335),  # sternum, midline, apex height
        (0.0, -0.20, 0.10),    # midline but far below: not the sternum at apex height
    ]
    anchor = bust_region_anchor(points, bone_head=(0.095, -0.155, 0.281), forward=forward)
    assert anchor == (0.10, -0.135, 0.34)
    # no midline vertex -> not a breast region
    assert bust_region_anchor(points[:3], (0.095, -0.155, 0.281), forward) is None
