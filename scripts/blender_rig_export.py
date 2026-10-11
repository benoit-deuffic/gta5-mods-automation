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
import re
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

    IMPORTANT CAVEAT (found 2026-10-11 doing a real full-body render): on a
    real multi-component ped like a_f_y_business_02, the body is a
    DICTIONARY of several separate per-component meshes (head, uppr, lowr,
    hair, hands...), not one single mesh -- and only ONE of them (whichever
    component the .yft Fragment itself carries, typically the head, for
    jaw/face breakable physics) comes in pre-skinned from Sollumz's import;
    the rest (uppr/lowr/hair) land as static, UNSKINNED meshes (0 vertex
    groups, parented to a plain Empty, no Armature modifier) even though
    their geometry is present and correctly positioned. This function only
    ever sees candidates that are ALREADY skinned -- call
    `rig_unskinned_body_components()` first if you need the other
    components considered too (e.g. for a full-body operation, not just
    "the one pre-skinned piece").
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


# Matches GTA V ped component-texture naming convention, e.g.
# "uppr_diff_000_a_whi" -> component="uppr", variant=0. Covers the
# component codes actually observed on a real extracted ped
# (a_f_y_business_02): head, uppr, lowr, hand, feet, hair, accs, teef.
_COMPONENT_TEXTURE_RE = re.compile(
    r"^(head|uppr|lowr|hand|feet|hair|accs|teef)_diff_(\d+)", re.IGNORECASE)


def classify_body_component(obj):
    """Infers (component, variant_index) from a mesh object's material
    name(s), using the GTA V ped texture naming convention
    "<component>_diff_<variant>_..." (e.g. "uppr_diff_000_a_whi").
    Returns None if no material matches -- e.g. for the armature itself,
    accessories, or objects that aren't a recognized ped body component.
    Verified against the real a_f_y_business_02 asset: its component
    meshes' material names follow this convention exactly.
    """
    for slot in obj.material_slots:
        if slot.material is None:
            continue
        m = _COMPONENT_TEXTURE_RE.match(slot.material.name)
        if m:
            return m.group(1).lower(), int(m.group(2))
    return None


def select_default_body_components(objects):
    """Groups ped body-component meshes by component type (head/uppr/lowr/
    hand/feet/hair/accs/teef) and picks the lowest-variant-index mesh per
    component as the "default" look -- a real ped's .ydd typically bundles
    several alternate variants of the same component (different clothing
    colors/hair styles) as separate Drawables, all landing as separate
    Blender objects at the same location; picking all of them would
    duplicate/overlap geometry (verified for real: a_f_y_business_02 has 2
    uppr variants, 2 lowr variants, 3 hair variants).

    Returns (selected: dict[str, bpy.types.Object], others: list[bpy.types.Object])
    -- `others` are the non-default variants, which the caller will
    typically want to hide (hide_render=True) so they don't appear in a
    render or get included in a bake/export pass.
    """
    by_component = {}
    for obj in objects:
        if obj.type != 'MESH':
            continue
        classified = classify_body_component(obj)
        if classified is None:
            continue
        component, variant = classified
        by_component.setdefault(component, []).append((variant, obj))

    selected = {}
    others = []
    for component, items in by_component.items():
        items.sort(key=lambda pair: pair[0])
        selected[component] = items[0][1]
        others.extend(obj for _variant, obj in items[1:])
    return selected, others


def rig_unskinned_body_components(armature, mesh_objects):
    """Binds any currently-unskinned mesh in `mesh_objects` to `armature`
    via Blender's automatic (heat-map) weight generation.

    WHY THIS EXISTS: real Sollumz imports of a multi-component ped only
    pre-skin the single Drawable embedded in the .yft Fragment (usually the
    head) -- other components (uppr/lowr/hair) land as static unskinned
    meshes even though the geometry itself is correct and in the right
    bind-pose position (verified for real on bora, 2026-10-11, by rendering
    a_f_y_business_02 before/after this fix: before, torso+legs were
    invisible/not part of any skinned hierarchy; after, a complete,
    correctly-posed, fully clothed body rendered with
    `bpy.ops.object.parent_set(type='ARMATURE_AUTO')`). This is NOT a
    missing-asset problem -- no extra extraction from the game is needed,
    the geometry is already there; it's purely a Sollumz-import/rigging gap
    this function closes.

    Objects already skinned (has at least one vertex group matching a bone
    name) are left untouched and NOT re-parented (re-running
    parent_set(ARMATURE_AUTO) on an already-skinned mesh would discard its
    real per-vertex weights and replace them with an approximate heat-map
    guess -- strictly worse). Returns the list of objects that were
    actually (re-)rigged by this call.
    """
    bone_names = set(b.name for b in armature.data.bones)
    to_rig = []
    for obj in mesh_objects:
        if obj.type != 'MESH':
            continue
        vgroup_names = set(g.name for g in obj.vertex_groups)
        if vgroup_names & bone_names:
            continue  # already skinned to this armature, leave it alone
        if len(obj.data.vertices) == 0:
            continue
        to_rig.append(obj)

    if not to_rig:
        return []

    bpy.ops.object.select_all(action='DESELECT')
    for obj in to_rig:
        obj.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    return to_rig


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
    # Rig any unskinned body-component meshes (uppr/lowr/hair -- real
    # Sollumz imports only pre-skin the .yft's own Drawable, see
    # rig_unskinned_body_components() docstring) BEFORE picking the
    # reference mesh, so find_reference_mesh() can see the whole rigged
    # body, not just the one piece Sollumz happened to skin automatically.
    if reference_armature:
        rig_unskinned_body_components(reference_armature, list(bpy.data.objects))
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
        # A plain imported mesh carries none of Sollumz's own custom
        # properties (sollumz_type etc.) -- export_assets silently exports
        # ONLY the armature's Fragment (.yft, the skeleton) and skips the
        # mesh entirely if it isn't recognized as a Sollumz Drawable Model,
        # so no .ydd/.ytd is ever produced for a foreign/rigged-in mesh
        # (found by actually running this against a real source mesh, not
        # just mocked bpy calls). sollumz.converttodrawable() wraps the mesh
        # into a proper Drawable > Drawable Model hierarchy so the exporter
        # recognizes and emits it. NOTE: the operator repurposes the
        # ORIGINAL object into an EMPTY (the new Drawable root) and creates
        # a NEW mesh object named "<original>.model" holding the actual
        # mesh data/modifiers/vertex groups (verified for real: modifiers
        # and vertex groups survive the conversion onto the new object) --
        # re-resolve both afterwards, the old `target_mesh` reference is no
        # longer a mesh.
        # A GTA V Fragment (.yft) supports exactly ONE Drawable -- the base
        # ped template's own original body mesh is already a Drawable
        # parented under this same armature, so exporting both produces a
        # Sollumz warning ("Fragment has multiple Drawables... only X will
        # be exported, Y ignored") and SILENTLY drops our new mesh, keeping
        # the untouched base ped body instead (found by actually running
        # this, not inferred). This step is a full geometry REPLACEMENT
        # pipeline (vs. the texture-only reskin path in
        # ai_texture_reskin.py), so the old body Drawable(s) must be removed
        # from the armature before export, after weight transfer has
        # already used reference_mesh as its source above.
        old_drawable_roots = {obj for obj in reference_armature.children if obj is not target_mesh}
        for root_obj in old_drawable_roots:
            for child in list(root_obj.children):
                bpy.data.objects.remove(child, do_unlink=True)
            bpy.data.objects.remove(root_obj, do_unlink=True)

        # A foreign imported mesh has no Sollumz-shader material -- export
        # aborts with "has no Sollumz materials! Aborting..." without one
        # (found by actually running this, not inferred). Give it a minimal
        # node-based material and convert it to the default Sollumz ped
        # shader via the addon's own operator (same one the Shader Manager
        # UI calls) rather than hand-building shader node graphs.
        if not target_mesh.data.materials:
            placeholder_mat = bpy.data.materials.new(f"{args.model_name}_mat")
            placeholder_mat.use_nodes = True
            target_mesh.data.materials.append(placeholder_mat)
        bpy.ops.object.select_all(action='DESELECT')
        target_mesh.select_set(True)
        bpy.context.view_layer.objects.active = target_mesh
        bpy.ops.sollumz.convertmaterialtoselected()

        mesh_original_name = target_mesh.name
        bpy.ops.object.select_all(action='DESELECT')
        target_mesh.select_set(True)
        bpy.context.view_layer.objects.active = target_mesh
        bpy.ops.sollumz.converttodrawable()
        drawable_root = bpy.data.objects[mesh_original_name]
        drawable_model = bpy.data.objects[f"{mesh_original_name}.model"]
        # sollumz.export_assets names its output file(s) after the exported
        # object(s), not after a filename parameter -- rename the armature
        # (and therefore its drawable dictionary) to --model-name so the
        # produced CWXML matches what scripts/codewalker_convert.py and
        # scripts/package_builder.py expect to find.
        reference_armature.name = args.model_name

        bpy.context.view_layer.objects.active = drawable_model
        for obj in bpy.data.objects:
            obj.select_set(obj in (drawable_root, drawable_model, reference_armature))
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
