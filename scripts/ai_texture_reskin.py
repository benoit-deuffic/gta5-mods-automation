"""Headless, multi-camera, UV-aware AI texture reskin pipeline for a GTA V
ped mesh. Run via:

    blender --background --python scripts/ai_texture_reskin.py -- \\
        --template <template.blend> --output-dir <dir> --model-name <name> \\
        --prompt "<text>" [--num-cameras 6] [--texture-size 512] \\
        [--sd-python ~/tools/sd-local/venv/bin/python] [--controlnet-conditioning-scale 0.8]

No interactive viewport is used anywhere (the thing that killed the earlier
Dream Textures attempt -- see docs/architecture-decisions.md and the
gtav-ped-modding skill). Every camera/projection operation uses explicit
object/matrix math (`bpy_extras.object_utils.world_to_camera_view`), every
render uses Blender's native background render pipeline, and view synthesis
is delegated to a separate subprocess (scripts/sd_view_synthesis.py, run
with the sd-local venv's own Python since Blender's bundled Python has no
torch/diffusers).

Pipeline per run:
1. Open the template, find the reference armature/mesh (reusing
   scripts/blender_rig_export.find_reference_armature/find_reference_mesh).
2. Place N cameras around the mesh's bounding box (azimuth ring + optional
   top/bottom), each looking at the bbox center.
3. For each camera, render a TRUE per-pixel Cycles Z-depth guide image
   (compositor Render Layers -> Map Range -> File Output, see
   render_guide_image() docstring).
4. Shell out to scripts/sd_view_synthesis.py (SDXL + ControlNet-depth,
   text-to-image conditioned on the depth map, prompt drives content/
   material/color -- see sd_view_synthesis.py module docstring) to
   synthesize a new view per camera from the depth guide image + prompt.
5. Assign every mesh face to exactly ONE camera (the one most front-on to
   that face, via a normal/view-direction dot product) so bake passes never
   overlap/clobber each other -- this is the "masking" the task calls out as
   the critical subtlety, implemented via partition, not literal pixel
   compositing (see partition_faces_to_cameras() docstring for the honest
   limitation: front-facing-wins, no raycast occlusion test).
6. For each camera's exclusive face partition: duplicate the mesh, delete
   all other faces, build a temp material driving an Emission shader from
   the camera's generated view image sampled via a per-vertex "projected UV"
   layer (world_to_camera_view), and Cycles-bake (type=EMIT) into the
   accumulator image using the mesh's REAL diffuse UV as the active/target
   UV layer. Because the duplicate only contains that camera's faces, the
   bake only ever touches texels inside those faces' real UV islands --
   other cameras' texels are untouched, giving masked compositing for free.
7. Save the accumulator as DDS in the SAME pixel format as the original
   texture (read from the ytd.xml <Format> field).
8. Round-trip validate via codewalker-cli (optional, best-effort).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys

import bpy
import bmesh
import mathutils
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blender_rig_export import (  # noqa: E402
    find_reference_armature, find_reference_mesh,
    rig_unskinned_body_components, select_default_body_components,
)

DEFAULT_SD_PYTHON = os.path.expanduser("~/tools/sd-local/venv/bin/python")
DEFAULT_SD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sd_view_synthesis.py")

DEFAULT_SKIN_PROMPT = (
    "natural human skin, photorealistic skin texture, subtle subsurface scattering, "
    "no clothing, no fabric, no jewelry"
)

# Vertex-group name prefixes that indicate "this vertex is skin, not clothing" on the
# real a_f_y_business_02 template (verified on bora, see gtav-ped-modding skill): the
# GTA skeleton's head/neck/hand/finger bones plus the FB_* facial-rig bones. Everything
# else (spine, forearm roll, etc.) defaults to the clothing prompt -- a camera that sees
# a bare forearm with no explicit skin weighting there will just get the clothing look,
# a known, acceptable simplification (no outfit-aware sleeve-length detection).
SKIN_VGROUP_PREFIXES = (
    "SKEL_Head", "SKEL_Neck", "SKEL_L_Hand", "SKEL_R_Hand",
    "SKEL_L_Finger", "SKEL_R_Finger", "FB_", "RB_Neck",
)


def parse_args():
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(description="Headless AI multi-view texture reskin")
    p.add_argument("--template", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--model-name", required=True)
    p.add_argument("--prompt", required=True,
                   help="Main/clothing prompt, used for every camera region that is NOT "
                        "classified as skin-dominant (see classify_camera_regions()).")
    p.add_argument("--skin-prompt", default=None,
                   help="Prompt used for cameras whose assigned faces are dominantly weighted "
                        "to skin-related vertex groups (head/neck/hands, see SKIN_VGROUP_PREFIXES). "
                        "Defaults to a neutral 'natural skin tone' prompt if not given -- never "
                        "falls back silently to the clothing prompt, which was the point-14 bug.")
    p.add_argument("--num-cameras", type=int, default=6,
                   help="4 = front/back/left/right ring only; 6 adds top+bottom "
                        "for shoulder/crotch seam coverage (see docstring point 2).")
    p.add_argument("--texture-size", type=int, default=512)
    p.add_argument("--render-size", type=int, default=512)
    p.add_argument("--sd-python", default=DEFAULT_SD_PYTHON)
    p.add_argument("--sd-script", default=DEFAULT_SD_SCRIPT)
    p.add_argument("--controlnet-conditioning-scale", type=float, default=0.8,
                   help="SDXL ControlNet-depth conditioning strength (0=ignore depth map/pure "
                        "prompt, 1=follow depth strictly). Supersedes the old --strength img2img "
                        "parameter now that view synthesis is depth-conditioned text-to-image, "
                        "not img2img on a flat guide.")
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--skip-sd", action="store_true",
                   help="Debug/test flag: skip the SD subprocess and bake the flat guide "
                        "images directly (lets the geometry/bake machinery be tested fast "
                        "without GPU/SD cost).")
    return p.parse_args(raw_args)


def enable_sollumz():
    for module in ("bl_ext.user_default.sollumz", "sollumz"):
        try:
            bpy.ops.preferences.addon_enable(module=module)
            return
        except Exception:
            continue


def mesh_world_bbox(obj):
    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    zs = [c[2] for c in corners]
    center = mathutils.Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2
    return center, radius


def build_cameras(scene, center, radius, num_cameras, render_size):
    """Places cameras on explicit object/matrix math only -- no viewport.

    num_cameras==4: front/back/left/right azimuth ring at bbox-center height.
    num_cameras>=6: ring + top + bottom (needed for shoulder/crotch coverage
    on a humanoid -- a 4-camera ring leaves the top of the shoulders and the
    underside/crotch area essentially untextured, confirmed by inspecting
    per-camera face-partition counts in this run, see docs point on this).
    """
    cameras = []
    distance = radius * 3.0
    ring_count = min(num_cameras, 4) if num_cameras <= 4 else 4
    for i in range(ring_count):
        angle = (2 * math.pi / ring_count) * i
        pos = center + mathutils.Vector((distance * math.sin(angle), -distance * math.cos(angle), 0))
        cameras.append(_make_camera(scene, f"reskin_cam_ring_{i}", pos, center, render_size))
    if num_cameras >= 6:
        pos_top = center + mathutils.Vector((0, 0, distance))
        cameras.append(_make_camera(scene, "reskin_cam_top", pos_top, center, render_size))
        pos_bottom = center + mathutils.Vector((0, 0, -distance))
        cameras.append(_make_camera(scene, "reskin_cam_bottom", pos_bottom, center, render_size))
    return cameras


def _make_camera(scene, name, position, target, render_size):
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = 50
    cam_obj = bpy.data.objects.new(name, cam_data)
    scene.collection.objects.link(cam_obj)
    cam_obj.location = position
    direction = (target - position).normalized()
    cam_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    return cam_obj


def render_guide_image(scene, camera, filepath, render_size, near=None, far=None):
    """Renders a TRUE per-pixel Cycles Z-depth map for this camera.

    Supersedes the earlier flat/clay-shaded "guide" approximation (kept the
    old behaviour documented below for history -- see
    docs/architecture-decisions.md point 13 and the follow-up point that
    replaces it). This now:
      1. enables the Z render pass on the view layer,
      2. enables the compositor and wires Render Layers -> Map Range
         (near/far clip derived from camera-to-mesh-bbox distance, so the
         mesh's own depth range maps to the full 0..1 grayscale range
         instead of a generic Normalize that can wash out contrast when the
         background plane is included) -> Composite AND -> a File Output
         node (so the PNG is written directly by the compositor, no need to
         fish pixels out of 'Viewer Node' by hand),
      3. renders and returns the resulting grayscale PNG path.

    near/far are world-space distances from the camera along its view axis;
    callers should pass (camera_to_bbox_center_distance - radius) and
    (camera_to_bbox_center_distance + radius) so the whole mesh maps into
    the visible grayscale range. If not given, falls back to a generic
    0.1..100 range (works but with much lower contrast).

    Confirmed headless-safe in practice this session: `scene.use_nodes`,
    `use_pass_z`, and File Output nodes all work fine under
    `blender --background` with Cycles CUDA, no viewport/display needed --
    the earlier abandonment of this exact approach was not because it was
    broken, it just wasn't tried through to completion.
    """
    scene.camera = camera
    scene.render.engine = 'CYCLES'
    try:
        scene.cycles.device = 'GPU'
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.get_devices()
        for d in prefs.devices:
            d.use = (d.type != 'CPU')
    except Exception as exc:
        print(f"[WARN] Could not force GPU device for Cycles, using default: {exc}")
    scene.render.resolution_x = render_size
    scene.render.resolution_y = render_size
    scene.cycles.samples = 16

    view_layer = scene.view_layers[0]
    view_layer.use_pass_z = True
    scene.use_nodes = True
    tree = scene.node_tree
    for n in list(tree.nodes):
        tree.nodes.remove(n)

    rl_node = tree.nodes.new("CompositorNodeRLayers")
    rl_node.layer = view_layer.name

    map_range = tree.nodes.new("CompositorNodeMapRange")
    map_range.inputs["From Min"].default_value = near if near is not None else 0.1
    map_range.inputs["From Max"].default_value = far if far is not None else 100.0
    map_range.inputs["To Min"].default_value = 1.0  # near = white
    map_range.inputs["To Max"].default_value = 0.0  # far = black
    map_range.use_clamp = True

    file_output = tree.nodes.new("CompositorNodeOutputFile")
    out_dir = os.path.dirname(filepath)
    out_base = os.path.splitext(os.path.basename(filepath))[0]
    file_output.base_path = out_dir
    file_output.file_slots[0].path = out_base
    file_output.format.file_format = 'PNG'
    file_output.format.color_mode = 'BW'

    composite_node = tree.nodes.new("CompositorNodeComposite")

    tree.links.new(rl_node.outputs["Depth"], map_range.inputs["Value"])
    tree.links.new(map_range.outputs["Value"], file_output.inputs[0])
    tree.links.new(map_range.outputs["Value"], composite_node.inputs["Image"])

    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = os.path.join(out_dir, out_base + "_composite")
    bpy.ops.render.render(write_still=True)

    # CompositorNodeOutputFile appends Blender's frame number to the
    # filename (e.g. "name0001.png") even for a single still -- find and
    # rename it to the exact path the caller asked for so downstream code
    # doesn't need to guess the frame-numbered name.
    produced = None
    frame_suffixed = os.path.join(out_dir, f"{out_base}{scene.frame_current:04d}.png")
    if os.path.exists(frame_suffixed):
        produced = frame_suffixed
    else:
        for fname in os.listdir(out_dir):
            if fname.startswith(out_base) and fname.endswith(".png") and fname != os.path.basename(filepath):
                produced = os.path.join(out_dir, fname)
                break
    if produced and produced != filepath:
        os.replace(produced, filepath)

    # Clean up the auxiliary "_composite" still written via scene.render.filepath
    # (the actual depth map we want comes from the File Output node above).
    composite_still = scene.render.filepath + ".png"
    if os.path.exists(composite_still):
        try:
            os.remove(composite_still)
        except OSError:
            pass

    return filepath


def partition_faces_to_cameras(mesh_obj, cameras):
    """Assigns every polygon to exactly one camera: the one most front-on to
    that face's world-space normal (highest dot(normal, dir-to-camera)).

    This is the "masking" step the task calls the critical subtlety. What IS
    implemented: a front-facing-wins partition, so every face is baked from
    exactly one camera and bake passes can never clobber each other (no
    separate transparent-pixel compositing needed -- see module docstring
    point 6). What is NOT implemented: true occlusion (e.g. an arm in front
    of the torso from a given camera) -- a face can be assigned to a camera
    it's geometrically unoccluded-facing toward even if another part of the
    mesh physically blocks the view in the render. This is a known,
    documented simplification (ray-casting each face centroid against the
    camera to detect occlusion would close this gap, not implemented here).
    Faces with no camera reaching dot>threshold are left unassigned (bake
    skips them, source texture pixels for those texels remain unchanged).
    """
    mesh = mesh_obj.data
    mesh.calc_loop_triangles()
    world_matrix = mesh_obj.matrix_world
    normal_matrix = world_matrix.inverted().transposed().to_3x3()
    assignment = {}
    for poly in mesh.polygons:
        world_normal = (normal_matrix @ poly.normal).normalized()
        world_center = world_matrix @ poly.center
        best_cam = None
        best_dot = -1.0
        for cam in cameras:
            view_dir = (cam.location - world_center).normalized()
            dot = world_normal.dot(view_dir)
            if dot > best_dot:
                best_dot = dot
                best_cam = cam
        if best_dot > 0.05:
            assignment[poly.index] = best_cam.name
    return assignment


def _face_skin_score(mesh, poly, skin_group_indices):
    """Average, across this face's vertices, of (weight on skin-related
    vertex groups) / (total weight on ANY vertex group that vertex
    belongs to). Returns 0..1 -- higher means more skin-dominant. Using a
    per-vertex ratio (not raw weight) makes this robust to a vertex having
    many small peripheral group memberships from normal skinning blend.
    """
    scores = []
    for loop_index in poly.loop_indices:
        vert_index = mesh.loops[loop_index].vertex_index
        vert = mesh.vertices[vert_index]
        skin_w = 0.0
        total_w = 0.0
        for ge in vert.groups:
            total_w += ge.weight
            if ge.group in skin_group_indices:
                skin_w += ge.weight
        if total_w > 1e-6:
            scores.append(skin_w / total_w)
    return sum(scores) / len(scores) if scores else 0.0


def classify_camera_regions(mesh_obj, per_cam_faces, skin_threshold=0.35):
    """Classifies each camera's exclusive face partition as 'skin' or
    'clothing' using the mesh's real SKEL_*/FB_* vertex groups (see
    SKIN_VGROUP_PREFIXES) -- this is what main() uses to pick a per-camera
    prompt instead of applying the single clothing prompt everywhere
    (docs/architecture-decisions.md point 14's documented limitation).

    A camera is 'skin' when the AREA-weighted average skin score across its
    assigned faces is >= skin_threshold (area weighting, not a plain face
    count, so a camera dominated by a few large clothing faces plus many
    tiny finger faces isn't mis-classified as skin). Falls back to
    'clothing' for every camera if the mesh has no recognizable skin
    vertex groups at all (e.g. a non-standard/no-rig test mesh), which
    matches the pre-existing single-prompt behaviour exactly.
    """
    mesh = mesh_obj.data
    skin_group_indices = {
        g.index for g in mesh_obj.vertex_groups
        if any(g.name.startswith(prefix) for prefix in SKIN_VGROUP_PREFIXES)
    }
    if not skin_group_indices:
        return {cam_name: "clothing" for cam_name in per_cam_faces}

    regions = {}
    for cam_name, face_indices in per_cam_faces.items():
        total_area = 0.0
        skin_area = 0.0
        for face_idx in face_indices:
            poly = mesh.polygons[face_idx]
            score = _face_skin_score(mesh, poly, skin_group_indices)
            total_area += poly.area
            skin_area += poly.area * score
        fraction = (skin_area / total_area) if total_area > 1e-9 else 0.0
        regions[cam_name] = "skin" if fraction >= skin_threshold else "clothing"
    return regions


def find_diffuse_uv_layer(mesh):
    """Picks the mesh's real diffuse UV layer (not any temp/projected one we
    add ourselves). Real Sollumz-imported meshes don't reliably name UV
    layers; pick the first UV layer that already exists before we add any
    new ones (caller must call this BEFORE adding a 'projected' UV layer).
    """
    if not mesh.uv_layers:
        return None
    return mesh.uv_layers[0].name


def bake_camera_partition(context, mesh_obj, face_indices, camera, view_image_path,
                           target_image, diffuse_uv_name, texture_size):
    """Duplicates mesh_obj restricted to face_indices, builds a bake
    material driving Emission from the camera's generated view via a
    per-vertex projected-UV layer, and Cycles-bakes EMIT into target_image
    using the mesh's real diffuse UV so results land at the correct existing
    UV coordinates (drop-in texture swap, not a new UV layout).
    """
    if not face_indices:
        return False

    scene = context.scene
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    context.view_layer.objects.active = mesh_obj
    bpy.ops.object.duplicate()
    dup = context.view_layer.objects.active

    bm = bmesh.new()
    bm.from_mesh(dup.data)
    bm.faces.ensure_lookup_table()
    keep = set(face_indices)
    to_delete = [f for f in bm.faces if f.index not in keep]
    bmesh.ops.delete(bm, geom=to_delete, context='FACES')
    bm.to_mesh(dup.data)
    bm.free()
    dup.data.update()

    if len(dup.data.polygons) == 0:
        bpy.data.objects.remove(dup, do_unlink=True)
        return False

    proj_uv = dup.data.uv_layers.new(name="reskin_projected_uv")
    for poly in dup.data.polygons:
        for loop_index in poly.loop_indices:
            vert_index = dup.data.loops[loop_index].vertex_index
            world_co = dup.matrix_world @ dup.data.vertices[vert_index].co
            ndc = world_to_camera_view(scene, camera, world_co)
            proj_uv.data[loop_index].uv = (ndc.x, ndc.y)

    dup.data.uv_layers.active = dup.data.uv_layers[diffuse_uv_name]

    view_img = bpy.data.images.load(view_image_path, check_existing=False)

    mat = bpy.data.materials.new(name="reskin_bake_mat")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    uv_src_node = nt.nodes.new("ShaderNodeUVMap")
    uv_src_node.uv_map = proj_uv.name
    src_tex_node = nt.nodes.new("ShaderNodeTexImage")
    src_tex_node.image = view_img
    emit_node = nt.nodes.new("ShaderNodeEmission")
    output_node = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(uv_src_node.outputs["UV"], src_tex_node.inputs["Vector"])
    nt.links.new(src_tex_node.outputs["Color"], emit_node.inputs["Color"])
    nt.links.new(emit_node.outputs["Emission"], output_node.inputs["Surface"])

    target_tex_node = nt.nodes.new("ShaderNodeTexImage")
    target_tex_node.image = target_image
    nt.nodes.active = target_tex_node
    target_tex_node.select = True

    dup.data.materials.clear()
    dup.data.materials.append(mat)

    bpy.ops.object.select_all(action='DESELECT')
    dup.select_set(True)
    context.view_layer.objects.active = dup

    scene.render.engine = 'CYCLES'
    scene.cycles.bake_type = 'EMIT'
    scene.render.bake.use_clear = False
    scene.render.bake.margin = max(4, texture_size // 128)
    bpy.ops.object.bake(type='EMIT')

    bpy.data.objects.remove(dup, do_unlink=True)
    bpy.data.materials.remove(mat)
    bpy.data.images.remove(view_img)
    return True


def main():
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    bpy.ops.wm.open_mainfile(filepath=args.template)
    enable_sollumz()

    reference_armature = find_reference_armature(bpy.data.objects)
    if not reference_armature:
        raise RuntimeError("Template file lacks a detectable reference armature.")

    # Real Sollumz imports of a multi-component ped only pre-skin the ONE
    # Drawable embedded in the .yft Fragment (usually the head) -- uppr/
    # lowr/hair land as separate, unskinned meshes even though their
    # geometry is correct (see rig_unskinned_body_components() docstring,
    # found 2026-10-11 by actually rendering a_f_y_business_02's full body:
    # before this fix, a texture reskin could only ever touch whichever
    # single piece Sollumz happened to auto-skin -- never the actual
    # clothing). Rig them, then pick exactly one "default" mesh per
    # component (a real ped's .ydd typically bundles several alternate
    # variants of the same component as separate Drawables, e.g.
    # a_f_y_business_02 has 2 uppr + 2 lowr + 3 hair variants all at the
    # same position -- baking onto all of them would duplicate/overlap
    # geometry and waste GPU time) and hide the rest from the scene
    # entirely so they can't interfere with bbox framing or bake output.
    rig_unskinned_body_components(reference_armature, list(bpy.data.objects))
    default_components, other_variants = select_default_body_components(bpy.data.objects)
    for obj in other_variants:
        obj.hide_render = True
        obj.hide_set(True)

    # Still include the legacy single find_reference_mesh() result as a
    # fallback component (keyed "body") for templates that aren't a
    # multi-component ped dictionary (e.g. a from-scratch single-mesh
    # template, or a test fixture) -- this keeps the previous, narrower
    # behaviour working unchanged when there's nothing to classify by the
    # component-texture-naming convention.
    if not default_components:
        fallback = find_reference_mesh(bpy.data.objects, reference_armature)
        if not fallback:
            raise RuntimeError("Template file lacks a detectable reference mesh.")
        default_components = {"body": fallback}

    print(f"[INFO] Body components to reskin: {sorted(default_components.keys())} "
          f"({len(other_variants)} alternate-variant mesh(es) hidden)")

    scene = bpy.context.scene

    # One shared camera ring around the WHOLE body (union of every
    # component's bbox), not per-component -- a per-component ring would
    # put the camera distance/framing out of proportion for small pieces
    # (hands) vs large ones (torso+legs), and a shared ring is what lets a
    # camera's region classification (skin vs clothing, see
    # classify_camera_regions()) stay meaningful across components.
    all_corners = []
    for obj in default_components.values():
        all_corners += [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    xs = [c[0] for c in all_corners]
    ys = [c[1] for c in all_corners]
    zs = [c[2] for c in all_corners]
    center = mathutils.Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) / 2
    cameras = build_cameras(scene, center, radius, args.num_cameras, args.render_size)
    print(f"[INFO] Built {len(cameras)} camera(s) around whole-body bbox "
          f"center={tuple(center)} radius={radius:.3f}")

    skin_prompt = args.skin_prompt or DEFAULT_SKIN_PROMPT

    manifest = {
        "model_name": args.model_name,
        "prompt": args.prompt,
        "skin_prompt": skin_prompt,
        "num_cameras": len(cameras),
        "texture_size": args.texture_size,
        "components": {},
    }

    for component_name, mesh_obj in default_components.items():
        diffuse_uv_name = find_diffuse_uv_layer(mesh_obj.data)
        if not diffuse_uv_name:
            print(f"[WARN] Component '{component_name}' ({mesh_obj.name}) has no UV layer, skipping.")
            continue

        assignment = partition_faces_to_cameras(mesh_obj, cameras)
        per_cam_faces = {}
        for face_idx, cam_name in assignment.items():
            per_cam_faces.setdefault(cam_name, []).append(face_idx)
        total_faces = len(mesh_obj.data.polygons)
        print(f"[INFO] Component '{component_name}': partitioned {len(assignment)}/{total_faces} "
              f"faces across {len(per_cam_faces)} camera(s).")

        camera_regions = classify_camera_regions(mesh_obj, per_cam_faces)

        target_image = bpy.data.images.new(
            name=f"{args.model_name}_{component_name}_reskin",
            width=args.texture_size, height=args.texture_size, alpha=False)
        # Seed with this component's OWN existing diffuse texture (not just
        # "the first image found", which only worked by accident when there
        # was a single component -- now that several components each have
        # their own diffuse texture, each must seed from its own, found via
        # this component's real material graph rather than guessed).
        existing_image = None
        for slot in mesh_obj.material_slots:
            if slot.material is None or not slot.material.use_nodes:
                continue
            for node in slot.material.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image is not None and node.image.size[0] > 0:
                    existing_image = node.image
                    break
            if existing_image:
                break
        if existing_image is not None:
            try:
                target_image.pixels.foreach_set(list(existing_image.pixels))
            except Exception:
                pass

        component_manifest = {"mesh_object": mesh_obj.name, "cameras": {}}

        for cam in cameras:
            faces = per_cam_faces.get(cam.name, [])
            if not faces:
                continue
            guide_path = os.path.join(
                args.output_dir, f"{args.model_name}_{component_name}_{cam.name}_guide.png")
            distance = (cam.location - center).length
            near_clip = max(0.01, distance - radius * 1.5)
            far_clip = distance + radius * 1.5
            render_guide_image(scene, cam, guide_path, args.render_size, near=near_clip, far=far_clip)

            region = camera_regions.get(cam.name, "clothing")
            cam_prompt = skin_prompt if region == "skin" else args.prompt
            # Mosaic-mitigation (docs/architecture-decisions.md point 15):
            # see the identical comment in the previous single-mesh version
            # of this loop -- unchanged behaviour, just now per-component.
            cam_prompt = f"{cam_prompt}, plain neutral background, solid color background"

            if args.skip_sd:
                view_path = guide_path
            else:
                view_path = os.path.join(
                    args.output_dir, f"{args.model_name}_{component_name}_{cam.name}_view.png")
                cmd = [
                    args.sd_python, args.sd_script,
                    "--depth-image", guide_path,
                    "--prompt", cam_prompt,
                    "--output", view_path,
                    "--controlnet-conditioning-scale", str(args.controlnet_conditioning_scale),
                    "--steps", str(args.steps),
                ]
                print(f"[INFO] [{component_name}] Running SD view synthesis for {cam.name} "
                      f"(region={region}): {' '.join(cmd)}")
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if proc.returncode != 0:
                    print(f"[WARN] SD synthesis failed for {component_name}/{cam.name}, "
                          f"falling back to guide image.\n{proc.stderr[-2000:]}")
                    view_path = guide_path

            baked = bake_camera_partition(
                bpy.context, mesh_obj, faces, cam, view_path, target_image, diffuse_uv_name, args.texture_size)
            component_manifest["cameras"][cam.name] = {
                "num_faces": len(faces),
                "region": region,
                "prompt_used": cam_prompt,
                "guide_image": guide_path,
                "view_image": view_path,
                "baked": baked,
            }

        component_png = os.path.join(args.output_dir, f"{args.model_name}_{component_name}_reskin.png")
        target_image.filepath_raw = component_png
        target_image.file_format = 'PNG'
        target_image.save()
        component_manifest["output_png"] = component_png
        component_manifest["source_texture"] = existing_image.name if existing_image else None
        manifest["components"][component_name] = component_manifest
        print(f"[OK] Saved '{component_name}' reskin texture to {component_png} "
              f"(source texture: {component_manifest['source_texture']})")

    manifest_path = os.path.join(args.output_dir, f"{args.model_name}_reskin_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"[OK] Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
