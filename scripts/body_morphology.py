"""Body morphology via bone-scale sliders -- the SAME technique GTA's own MP
Freemode character creator uses for body sliders (not a free-form mesh
sculpt/Lattice system): named sliders map to specific skeleton bones, and
scaling those bones via pose-mode + "Apply Pose as Rest Pose" reshapes the
skinned mesh proportionally while leaving topology/vertex weights
untouched -- critical, since it means existing clothing components stay
validly skinned to the SAME skeleton (see MORPH_SLIDERS docstring below for
the real limitation this doesn't solve: clothing meshes authored for the
default proportions will still clip if a slider pushes far from 1.0).

This module is split into a PURE part (no bpy -- slider validation,
clamping, and resolving which bones each slider touches, with multiple
sliders affecting the same bone multiplying together) and a bpy-dependent
part (apply_body_morphology, actually touches the Blender scene). Only the
pure part is unit-testable without a real Blender process; the bpy part was
verified for real against templates/template_female_a_f_y_business_02.blend
on 2026-10-12 (see docs/architecture-decisions.md).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Real bone names confirmed against the actual GTA V human skeleton
# (templates/template_female_a_f_y_business_02.blend, 104 bones, verified
# 2026-10-12). Each slider lists the bone(s) it scales uniformly (X=Y=Z).
# This is a DELIBERATELY SMALL initial vocabulary -- not every bone is
# exposed as a slider, only ones that produce a recognizable, generally-
# safe body-shape change without immediately breaking the silhouette.
MORPH_SLIDERS: dict[str, tuple[str, ...]] = {
    "height": ("SKEL_ROOT",),
    "shoulder_width": ("SKEL_L_Clavicle", "SKEL_R_Clavicle"),
    "torso_bulk": ("SKEL_Spine0", "SKEL_Spine1", "SKEL_Spine2", "SKEL_Spine3"),
    "waist_width": ("SKEL_Pelvis",),
    "leg_length": ("SKEL_L_Thigh", "SKEL_R_Thigh", "SKEL_L_Calf", "SKEL_R_Calf"),
    "arm_bulk": ("SKEL_L_UpperArm", "SKEL_R_UpperArm", "SKEL_L_Forearm", "SKEL_R_Forearm"),
    "bust_size": ("SPR_L_Breast", "SPR_R_Breast"),
}

# Clamp range for every slider value -- scaling a bone much past this tends
# to visibly break the mesh (self-intersection, skin stretching artifacts)
# even with otherwise-correct weights; found by inspection, not a hard
# engine limit. 1.0 = unchanged.
SLIDER_MIN = 0.6
SLIDER_MAX = 1.6


def _clamp(value: float) -> float:
    return max(SLIDER_MIN, min(SLIDER_MAX, value))


def build_pose_bone_scale_map(slider_values: dict[str, float]) -> dict[str, float]:
    """Pure logic (no bpy): resolves a dict of {slider_name: value} into a
    dict of {bone_name: combined_scale}, used by apply_body_morphology().

    - Unknown slider names are skipped with a warning (not a crash) --
      e.g. if an LLM-driven caller hallucinates a slider name outside
      MORPH_SLIDERS.
    - Each slider's value is clamped to [SLIDER_MIN, SLIDER_MAX] before use.
    - If two sliders affect the SAME bone (none currently do in
      MORPH_SLIDERS, but a future slider addition might overlap, e.g. a
      hypothetical "posture" slider also touching SKEL_Spine1), their
      scales MULTIPLY rather than overwrite, so the caller never silently
      loses one slider's effect to another.
    """
    bone_scales: dict[str, float] = {}
    for slider_name, raw_value in slider_values.items():
        bones = MORPH_SLIDERS.get(slider_name)
        if bones is None:
            logger.warning("unknown body morphology slider %r, skipped", slider_name)
            continue
        value = _clamp(raw_value)
        for bone_name in bones:
            bone_scales[bone_name] = bone_scales.get(bone_name, 1.0) * value
    return bone_scales


def apply_body_morphology(armature, slider_values: dict[str, float]) -> dict:
    """bpy-dependent: applies `slider_values` (see MORPH_SLIDERS for valid
    keys) to `armature`'s pose bones, then PERMANENTLY bakes the resulting
    deformation into each affected mesh's actual vertex data.

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

    Returns {"applied_bones": {...}, "skipped_bones": [...],
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
        pose_bone.scale = (scale, scale, scale)
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
