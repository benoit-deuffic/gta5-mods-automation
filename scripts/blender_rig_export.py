"""Headless Blender rig/weight-transfer/export script run via:

    blender --background --python scripts/blender_rig_export.py -- <args>

Corrections applied vs. the original spec script (docs/analyse-prerequis.md §2.2):
1. `bpy.ops.sollumz.export_ped` does not exist -> use `bpy.ops.sollumz.export_assets`.
2. `vert_mapping='POLY_NEAREST_VN'` is not a valid DataTransfer value -> use
   'POLYINTERP_VNORPROJ' and set layers_vgroup_select_src/dst + use_object_transform.
3. Fixed operator-precedence bug when searching for the reference mesh
   (`"head" in x.lower() or "body" in x.lower()` must be grouped).
4. Added pre-transfer alignment: scale the imported mesh to the reference mesh's
   bounding box before projecting weights (unaligned meshes => garbage weights
   => limb stretching during animation, acceptance criterion 4).
5. Added post-transfer weight normalization + 4-influence limit (RAGE skinning
   limit) via vertex_group_limit_total + vertex_group_normalize_all.
6. Native Sollumz binary export (pymateria) is Windows-only; on Linux this
   script produces CodeWalker XML (.ydd.xml/.yft.xml/.ytd.xml) that must be
   converted to binary separately (scripts/codewalker_convert.py).
7. Accepts both FBX (Meshy) and GLB/glTF (Tripo3D) source meshes, picked by
   file extension -- Tripo3D's text-to-model output is a single GLB with
   embedded PBR textures, no separate texture files (see
   mcp_server/tripo_client.py). UNTESTED against a real GLB: this sandbox has
   no Blender to run it against; verify on the GPU workstation before relying
   on it (docs/architecture-decisions.md point 9).
"""
import sys
import argparse
import os
import bpy

IMPORTABLE_EXTENSIONS = {".fbx", ".glb", ".gltf"}


def parse_args():
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Headless GTA V Rig & Weight Transfer")
    parser.add_argument("--template", required=True, help="Path to base ped template .blend")
    parser.add_argument("--source-fbx", "--source-mesh", dest="source_mesh", required=True,
                         help="Path to input raw mesh from the 3D backend: .fbx (Meshy) or .glb/.gltf (Tripo3D)")
    parser.add_argument("--output-dir", required=True, help="Destination folder for exported assets")
    parser.add_argument("--model-name", required=True, help="Model identifier")
    parser.add_argument("--vert-mapping", default="POLYINTERP_VNORPROJ")
    parser.add_argument("--max-influences", type=int, default=4)
    return parser.parse_args(raw_args)


def import_source_mesh(path: str):
    """Import the raw AI-generated mesh regardless of which backend produced
    it. Returns the list of objects selected by the import operator (same
    contract for both branches, so the caller doesn't need to know which
    backend was used).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMPORTABLE_EXTENSIONS:
        raise RuntimeError(f"Unsupported source mesh extension {ext!r} for {path} "
                            f"(expected one of {sorted(IMPORTABLE_EXTENSIONS)}).")
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path, use_anim=False)
    else:
        # Tripo3D GLB: PBR textures are embedded, Blender's glTF importer
        # unpacks them into image datablocks automatically -- no separate
        # texture files to feed the validator/manifest step for this path.
        bpy.ops.import_scene.gltf(filepath=path)
    return bpy.context.selected_objects


def find_reference_armature(objects):
    """Picks the GTA V skeleton armature from a real Sollumz-imported ped
    template. Real Sollumz imports name the armature after the model file
    (e.g. "a_f_y_business_02"), NOT "Armature"/"skel" as originally assumed
    -- verified against a real a_f_y_business_02.yft/.ydd import on bora
    (2026-10-11): produces an armature with 104 bones plus a couple of
    smaller auxiliary armature objects. Picking by highest bone count is
    robust regardless of naming convention.
    """
    armatures = [o for o in objects if o.type == 'ARMATURE']
    if not armatures:
        return None
    return max(armatures, key=lambda o: len(o.data.bones))


def find_reference_mesh(candidates, reference_armature=None):
    """Picks the main skinned body mesh from a real ped template.

    Real Sollumz-imported peds do NOT reliably name meshes "head"/"body"
    (verified: a real import produced meshes named "SKEL_ROOT",
    "SKEL_ROOT.001", "hash_XXXXXXXX.model", etc. -- hash-named when no name
    table is loaded). The reliable signal is: a MESH object whose vertex
    groups actually correspond to the reference armature's bones (i.e. it's
    skinned), picking the one with the most vertices (the main body LOD,
    not a prop/accessory).
    """
    bone_names = set(b.name for b in reference_armature.data.bones) if reference_armature else None
    best = None
    best_verts = -1
    for obj in candidates:
        if obj.type != 'MESH':
            continue
        if bone_names is not None:
            vgroup_names = set(g.name for g in obj.vertex_groups)
            if not (vgroup_names & bone_names):
                continue  # not skinned to this armature at all
        else:
            name = obj.name.lower()
            if not ("head" in name or "body" in name):
                continue
        nverts = len(obj.data.vertices)
        if nverts > best_verts:
            best = obj
            best_verts = nverts
    return best


def scale_to_reference_bbox(target_mesh, reference_mesh):
    """Align the imported mesh's bounding box to the reference before weight transfer.

    Without this, meshes generated at an arbitrary scale/pose produce nonsense
    vertex weights when projected onto the GTA skeleton (acceptance criterion 4:
    'no infinite polygon stretching'). This only corrects overall scale; pose
    alignment (T/A-pose) must come from Meshy's `pose_mode` parameter upstream.
    """
    import mathutils

    def bbox_dims(obj):
        corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        zs = [c[2] for c in corners]
        return max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)

    tx, ty, tz = bbox_dims(target_mesh)
    rx, ry, rz = bbox_dims(reference_mesh)
    if min(tx, ty, tz) <= 1e-6:
        print("[WARN] Degenerate target bbox, skipping auto-scale.")
        return
    # Use height (Z) as the dominant scale axis for humanoids.
    scale_factor = rz / tz if tz > 1e-6 else 1.0
    target_mesh.scale = mathutils.Vector((scale_factor, scale_factor, scale_factor))
    bpy.context.view_layer.update()


def main():
    args = parse_args()

    bpy.ops.wm.open_mainfile(filepath=args.template)

    # Addon enablement lives in Blender's user preferences, not in the
    # .blend file itself, so it never "sticks" across a fresh
    # `blender --background --python ...` invocation even if it was enabled
    # interactively before saving the template. Must be (re-)enabled here
    # every run. Blender 4.2+ extension id; falls back to the pre-4.2
    # legacy addon module name for older Blender/Sollumz combos.
    for module in ("bl_ext.user_default.sollumz", "sollumz"):
        try:
            bpy.ops.preferences.addon_enable(module=module)
            break
        except Exception:
            continue

    reference_armature = find_reference_armature(bpy.data.objects)
    reference_mesh = find_reference_mesh(bpy.data.objects, reference_armature)
    if not reference_armature or not reference_mesh:
        raise RuntimeError("Template file lacks expected Armature or Base Mesh objects.")

    imported_objects = import_source_mesh(args.source_mesh)
    target_mesh = next((obj for obj in imported_objects if obj.type == 'MESH'), None)
    if not target_mesh:
        raise RuntimeError(f"No valid mesh geometry found in imported {args.source_mesh}.")

    bpy.context.view_layer.objects.active = target_mesh
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    scale_to_reference_bbox(target_mesh, reference_mesh)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    for group in reference_mesh.vertex_groups:
        if group.name not in target_mesh.vertex_groups:
            target_mesh.vertex_groups.new(name=group.name)

    modifier = target_mesh.modifiers.new(name="WeightTransfer", type='DATA_TRANSFER')
    modifier.object = reference_mesh
    modifier.use_vert_data = True
    modifier.data_types_verts = {'VGROUP_WEIGHTS'}
    modifier.vert_mapping = args.vert_mapping
    modifier.layers_vgroup_select_src = 'ALL'
    modifier.layers_vgroup_select_dst = 'NAME'
    modifier.use_object_transform = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)

    bpy.context.view_layer.objects.active = target_mesh
    bpy.ops.object.vertex_group_normalize_all(lock_active=False)
    bpy.ops.object.vertex_group_limit_total(limit=args.max_influences)
    bpy.ops.object.vertex_group_normalize_all(lock_active=False)

    if len(target_mesh.data.vertices) > 65535:
        print(f"[WARN] {target_mesh.name} has {len(target_mesh.data.vertices)} verts "
              f"(> RAGE 65535 limit per geometry). Needs splitting before export.")

    target_mesh.parent = reference_armature
    armature_mod = target_mesh.modifiers.new(name="Armature", type='ARMATURE')
    armature_mod.object = reference_armature

    export_path = f"{args.output_dir}/{args.model_name}"
    addon_key = next((k for k in bpy.context.preferences.addons.keys() if "sollumz" in k.lower()), None)
    if addon_key:
        # sollumz.export_assets names its output file(s) after the exported
        # object(s), not after a filename parameter -- rename the armature
        # (and therefore its drawable dictionary) to --model-name so the
        # produced CWXML matches what scripts/codewalker_convert.py and
        # scripts/package_builder.py expect to find.
        reference_armature.name = args.model_name

        bpy.context.view_layer.objects.active = target_mesh
        for obj in bpy.data.objects:
            obj.select_set(obj in (target_mesh, reference_armature))
        # Real operator (spec used the non-existent sollumz.export_ped):
        bpy.ops.sollumz.export_assets(
            directory=args.output_dir,
            direct_export=True,
            use_custom_settings=True,
            target_formats={'CWXML'},   # NATIVE binary needs pymateria, Windows-only
            target_versions={'GEN8'},
            limit_to_selected=True,
            exclude_skeleton=False,
        )
        print(f"[OK] Exported CodeWalker-XML assets for '{args.model_name}' to {args.output_dir}. "
              f"Run scripts/codewalker_convert.py to produce binary .ydd/.yft/.ytd.")
    else:
        print("[WARN] Sollumz addon not present. Exporting placeholder OBJ for pipeline validation.")
        bpy.ops.wm.obj_export(filepath=f"{export_path}.obj")


if __name__ == "__main__":
    main()
