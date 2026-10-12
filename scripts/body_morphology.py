"""Body morphology via bone-scale sliders -- the SAME technique GTA's own MP
Freemode character creator uses for body sliders (not a free-form mesh
sculpt/Lattice system): named sliders map to specific skeleton bones AND a
specific LOCAL AXIS of that bone, and scaling via pose-mode + baking the
Armature modifier reshapes the skinned mesh proportionally while leaving
topology/vertex weights untouched -- critical, since it means existing
clothing components stay validly skinned to the SAME skeleton (see
MORPH_SLIDERS docstring below for the real limitation this doesn't solve:
clothing meshes authored for the default proportions will still clip if a
slider pushes far from 1.0).

PER-AXIS PRECISION (added 2026-10-12, see docs/architecture-decisions.md):
each bone's local X/Y/Z axes do NOT consistently map to world
width/depth/height across different bones -- confirmed empirically against
the real templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend asset
(a form-fitting MP Freemode outfit mesh, chosen specifically because the
earlier business_02 test ped's rigid blazer barely responded to ANY breast
bone scaling at all -- a structured jacket legitimately shouldn't show
much). Measured per-axis effect on that mesh's real world bounding box
(2x/1.5x test scale):
  SPR_L/R_Breast local X -> world X (+8%, "bust_width")
  SPR_L/R_Breast local Y -> world Y (+12%, "bust_projection")
  SPR_L/R_Breast local Z -> world Z (+15%, cleanest single-axis "bust_size")
  SKEL_Pelvis    local Y -> world Y (+12%, "hip_depth")
  SKEL_Pelvis    local Z -> world X (+12%, "hip_width" -- note the axis
                                      swap vs breast, each bone has its own
                                      local orientation, do NOT assume a
                                      consistent axis<->direction mapping
                                      across different bones)
  SKEL_Pelvis    local X -> world Z (+0.4%, negligible, not exposed)
No dedicated glute/buttock bone exists in this skeleton (confirmed: only
SPR_L/R_Breast are dedicated "soft prop" bones) -- "hip_width" via
SKEL_Pelvis is the closest available lever, but it is NOT an isolated
glute control: it also shifts the thigh attachment point and general
hip/waist region together, since SKEL_Pelvis parents SKEL_L/R_Thigh. A
truly isolated glute slider would need a vertex-group-based regional
deformation (e.g. a Lattice bound to a bounding-box-selected vertex group),
not a bone-scale lever -- out of scope here, flagged as a known gap.

This module is split into a PURE part (no bpy -- slider validation,
clamping, and resolving which bones+axes each slider touches, with
multiple sliders affecting the same bone+axis multiplying together) and a
bpy-dependent part (apply_body_morphology, actually touches the Blender
scene). Only the pure part is unit-testable without a real Blender
process; the bpy part was verified for real against
templates/template_mp_f_freemode_01_mp_f_bikerdlc_01.blend on 2026-10-12.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Real bone names confirmed against the actual GTA V human skeleton
# (104 bones, shared by every ped -- verified against both
# template_female_a_f_y_business_02.blend and
# template_mp_f_freemode_01_mp_f_bikerdlc_01.blend, 2026-10-12). Each
# slider maps to {bone_name: (x_mask, y_mask, z_mask)} -- the mask marks
# which of the BONE'S OWN local axes this slider scales (1 = scaled by the
# slider's value, 0 = left at 1.0 / untouched). See the module docstring
# for the empirically-measured per-axis effect of each entry below.
#
# This is a DELIBERATELY SMALL initial vocabulary -- not every bone is
# exposed as a slider, only ones that produce a recognizable, generally-
# safe body-shape change without immediately breaking the silhouette.
MORPH_SLIDERS: dict[str, dict[str, tuple[float, float, float]]] = {
    "height": {"SKEL_ROOT": (1, 1, 1)},
    "shoulder_width": {"SKEL_L_Clavicle": (1, 1, 1), "SKEL_R_Clavicle": (1, 1, 1)},
    "torso_bulk": {
        "SKEL_Spine0": (1, 1, 1), "SKEL_Spine1": (1, 1, 1),
        "SKEL_Spine2": (1, 1, 1), "SKEL_Spine3": (1, 1, 1),
    },
    "leg_length": {
        "SKEL_L_Thigh": (1, 1, 1), "SKEL_R_Thigh": (1, 1, 1),
        "SKEL_L_Calf": (1, 1, 1), "SKEL_R_Calf": (1, 1, 1),
    },
    "arm_bulk": {
        "SKEL_L_UpperArm": (1, 1, 1), "SKEL_R_UpperArm": (1, 1, 1),
        "SKEL_L_Forearm": (1, 1, 1), "SKEL_R_Forearm": (1, 1, 1),
    },
    # Precise, isolated bust control (replaces the old uniform "bust_size").
    "bust_width": {"SPR_L_Breast": (1, 0, 0), "SPR_R_Breast": (1, 0, 0)},
    "bust_projection": {"SPR_L_Breast": (0, 1, 0), "SPR_R_Breast": (0, 1, 0)},
    "bust_size": {"SPR_L_Breast": (0, 0, 1), "SPR_R_Breast": (0, 0, 1)},
    # Hip/waist control via the pelvis -- see module docstring: NOT an
    # isolated glute control, the closest available lever without a
    # vertex-group-based regional system (no dedicated glute bone exists).
    "hip_width": {"SKEL_Pelvis": (0, 0, 1)},
    "hip_depth": {"SKEL_Pelvis": (0, 1, 0)},
}

# Clamp range for every slider value -- scaling a bone much past this tends
# to visibly break the mesh (self-intersection, skin stretching artifacts)
# even with otherwise-correct weights; found by inspection, not a hard
# engine limit. 1.0 = unchanged.
SLIDER_MIN = 0.6
SLIDER_MAX = 1.6


def _clamp(value: float) -> float:
    return max(SLIDER_MIN, min(SLIDER_MAX, value))


def build_pose_bone_scale_map(slider_values: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """Pure logic (no bpy): resolves a dict of {slider_name: value} into a
    dict of {bone_name: (x_scale, y_scale, z_scale)}, used by
    apply_body_morphology().

    - Unknown slider names are skipped with a warning (not a crash) --
      e.g. if an LLM-driven caller hallucinates a slider name outside
      MORPH_SLIDERS.
    - Each slider's value is clamped to [SLIDER_MIN, SLIDER_MAX] before use.
    - A slider only scales the axes marked 1 in its MORPH_SLIDERS entry;
      masked-out axes contribute a factor of 1.0 (no-op) for that slider.
    - If two sliders affect the SAME axis of the SAME bone (e.g. a future
      slider addition overlapping an existing one), their scales MULTIPLY
      rather than overwrite, so the caller never silently loses one
      slider's effect to another.
    """
    bone_scales: dict[str, list[float]] = {}
    for slider_name, raw_value in slider_values.items():
        bone_axis_masks = MORPH_SLIDERS.get(slider_name)
        if bone_axis_masks is None:
            logger.warning("unknown body morphology slider %r, skipped", slider_name)
            continue
        value = _clamp(raw_value)
        for bone_name, mask in bone_axis_masks.items():
            current = bone_scales.setdefault(bone_name, [1.0, 1.0, 1.0])
            for axis in range(3):
                if mask[axis]:
                    current[axis] *= value
    return {bone: (scale[0], scale[1], scale[2]) for bone, scale in bone_scales.items()}


def apply_body_morphology(armature, slider_values: dict[str, float]) -> dict:
    """bpy-dependent: applies `slider_values` (see MORPH_SLIDERS for valid
    keys) to `armature`'s pose bones (per-axis, see build_pose_bone_scale_map),
    then PERMANENTLY bakes the resulting deformation into each affected
    mesh's actual vertex data.

    PITFALL found 2026-10-12 (initial implementation used
    `bpy.ops.pose.armature_apply()`, which looked right but does something
    different): that operator redefines the ARMATURE's rest pose to match
    the current pose -- it does NOT re-bake mesh vertex data. Since the
    Armature modifier deforms a mesh based on "current pose relative to
    rest pose", and armature_apply makes pose == rest pose again, the net
    visible deformation silently becomes ZERO right after "applying" it
    (confirmed by a minimal repro: pose bone scale correctly resets to
    (1,1,1) as if it worked, but cube.data.vertices never changes at all).
    That operator is for a different use case (turning a posed animation
    frame into a new T-pose for retargeting), not permanent reshaping.

    The actually-correct approach, confirmed by the same repro once fixed:
    apply the ARMATURE MODIFIER itself on each affected mesh object
    (bpy.ops.object.modifier_apply) -- this bakes the live pose-induced
    deformation directly into that mesh's vertex coordinates. A fresh
    Armature modifier is then added back (same armature, same implicit
    per-bone vertex group binding -- vertex groups are mesh data and
    survive modifier_apply untouched) so Sollumz still recognizes the mesh
    as skinned for export. Pose bone scales are reset to 1.0 afterwards so
    the shape is the new static baseline, not re-applied cumulatively next
    time this function runs.

    KNOWN LIMITATION, not solved by this function: clothing component
    meshes (uppr/lowr/jbib/...) are skinned to the SAME skeleton but were
    authored for the UNSCALED proportions -- pushing a slider far from 1.0
    will make existing clothing clip/float relative to the new body shape.
    Keeping sliders close to 1.0 (within roughly +/-15%) minimizes this;
    a true fix would need to re-fit clothing meshes too (out of scope here).

    Returns {"applied_bones": {bone: (x,y,z), ...}, "skipped_bones": [...],
    "reshaped_meshes": [mesh_name, ...]} for the caller to log/report.
    """
    import bpy  # local import: this module must stay importable without bpy
    # for the pure-logic half (build_pose_bone_scale_map) to be unit-tested.

    bone_scale_map = build_pose_bone_scale_map(slider_values)
    bone_names = {b.name for b in armature.data.bones}

    applied = {}
    skipped = []
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    for bone_name, scale in bone_scale_map.items():
        if bone_name not in bone_names:
            skipped.append(bone_name)
            continue
        pose_bone = armature.pose.bones[bone_name]
        pose_bone.scale = scale
        applied[bone_name] = scale
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()

    reshaped = []
    for obj in list(armature.children):
        if obj.type != 'MESH':
            continue
        armature_mod = next((m for m in obj.modifiers if m.type == 'ARMATURE' and m.object is armature), None)
        if armature_mod is None:
            continue
        bpy.context.view_layer.objects.active = obj
        for o in bpy.data.objects:
            o.select_set(o is obj)
        bpy.ops.object.modifier_apply(modifier=armature_mod.name)
        new_mod = obj.modifiers.new("Armature", 'ARMATURE')
        new_mod.object = armature
        reshaped.append(obj.name)

    # The shape is now permanently baked into mesh data -- reset the pose so
    # it isn't cumulatively re-applied the next time this function runs.
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    for bone_name in bone_scale_map:
        if bone_name in bone_names:
            armature.pose.bones[bone_name].scale = (1.0, 1.0, 1.0)
    bpy.ops.object.mode_set(mode='OBJECT')

    return {"applied_bones": applied, "skipped_bones": skipped, "reshaped_meshes": reshaped}
