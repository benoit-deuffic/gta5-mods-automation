"""Tests build_pose_bone_scale_map() in scripts/body_morphology.py -- the
pure-logic half of the bone-scale body morphology system (no bpy needed).
apply_body_morphology() itself (the bpy-dependent half) was verified for
real against templates/template_female_a_f_y_business_02.blend on
2026-10-12 -- see docs/architecture-decisions.md.
"""
from scripts.body_morphology import (
    MORPH_SLIDERS, SLIDER_MIN, SLIDER_MAX, build_pose_bone_scale_map,
)


def test_single_slider_maps_to_its_bones():
    result = build_pose_bone_scale_map({"shoulder_width": 1.2})
    assert result == {"SKEL_L_Clavicle": 1.2, "SKEL_R_Clavicle": 1.2}


def test_unknown_slider_is_skipped_without_crashing():
    result = build_pose_bone_scale_map({"totally_made_up_slider": 2.0, "height": 1.1})
    assert result == {"SKEL_ROOT": 1.1}


def test_value_clamped_to_slider_max():
    result = build_pose_bone_scale_map({"height": 5.0})
    assert result == {"SKEL_ROOT": SLIDER_MAX}


def test_value_clamped_to_slider_min():
    result = build_pose_bone_scale_map({"height": -3.0})
    assert result == {"SKEL_ROOT": SLIDER_MIN}


def test_default_unchanged_value_is_one():
    result = build_pose_bone_scale_map({"height": 1.0})
    assert result == {"SKEL_ROOT": 1.0}


def test_multiple_independent_sliders_combine():
    result = build_pose_bone_scale_map({"height": 1.1, "leg_length": 0.9})
    assert result["SKEL_ROOT"] == 1.1
    for bone in ("SKEL_L_Thigh", "SKEL_R_Thigh", "SKEL_L_Calf", "SKEL_R_Calf"):
        assert result[bone] == 0.9


def test_overlapping_sliders_on_same_bone_multiply_not_overwrite():
    # Construct a synthetic overlap by calling the map-building logic twice
    # with sliders that happen to share a bone target, to lock down the
    # documented "multiply, don't overwrite" behavior even though no two
    # CURRENT entries in MORPH_SLIDERS actually overlap yet.
    import scripts.body_morphology as module
    try:
        module.MORPH_SLIDERS["_test_overlap_a"] = ("SKEL_Spine1",)
        module.MORPH_SLIDERS["_test_overlap_b"] = ("SKEL_Spine1",)
        result = module.build_pose_bone_scale_map({"_test_overlap_a": 1.2, "_test_overlap_b": 1.1})
        assert result["SKEL_Spine1"] == 1.2 * 1.1
    finally:
        del module.MORPH_SLIDERS["_test_overlap_a"]
        del module.MORPH_SLIDERS["_test_overlap_b"]


def test_empty_input_produces_empty_map():
    assert build_pose_bone_scale_map({}) == {}


def test_all_documented_sliders_are_non_empty_bone_tuples():
    # Guards against a typo leaving a slider with an empty/missing bone list.
    for slider_name, bones in MORPH_SLIDERS.items():
        assert isinstance(bones, tuple) and len(bones) > 0, slider_name
        for bone in bones:
            assert isinstance(bone, str) and bone.startswith(("SKEL_", "SPR_")), (slider_name, bone)
