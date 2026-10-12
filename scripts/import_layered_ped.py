"""Full layered-ped construction pipeline, run headless via:

    blender --background --python scripts/import_layered_ped.py -- \\
        --template templates/template_female_a_f_y_business_02.blend \\
        --xml-root /path/to/exported/xml/tree \\
        --ped-root /path/to/exported/xml/tree/<ped_name_parent> \\
        --ped-name mp_f_freemode_01_mp_f_bikerdlc_01 \\
        --output-blend workspace/mp_f_freemode_01_mp_f_bikerdlc_01.blend \\
        [--preview-png workspace/preview.png]

Ties together, as permanent code, the three pieces proven separately this
session (2026-10-12):
  1. layered_ped_builder.resolve_layered_components() -- picks the winning
     file per component code across the base_all/mid/top tiers (top wins,
     per the user's explicit rule: "le niveau le plus haut doit etre le
     rendu final dans gta").
  2. Sollumz's bpy.ops.sollumz.import_assets() onto an existing template's
     skeleton (this asset family has no .yft/skeleton of its own) +
     blender_rig_export.rig_unskinned_body_components() (only the
     Fragment's own Drawable comes in pre-skinned; everything else needs
     auto-weighting).
  3. blender_rig_export.relink_external_textures() -- Sollumz imports each
     Image with a RELATIVE path that never resolves against the real
     exported-XML directory tree; without this every component renders in
     a flat, undetailed color instead of its real diffuse texture.

The input to this script is the CodeWalker-XML form (.ydd.xml), NOT raw
game binaries -- run scripts/codewalker_convert.export_tree_to_xml() first
to produce --xml-root from the user's raw peds/<ped_name>/ extraction.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_rig_export as bre  # noqa: E402
from layered_ped_builder import (  # noqa: E402
    resolve_layered_components,
    resolve_layered_components_with_body_donor,
)


def parse_args():
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Layered ped construction (base_all/mid/top)")
    parser.add_argument("--template", required=True, help="Path to base ped template .blend "
                                                            "(provides the skeleton this asset family lacks)")
    parser.add_argument("--ped-root", required=True, help="Directory directly containing <ped-name>/ "
                                                            "and <ped-name>.ymt -- see "
                                                            "layered_ped_builder's module docstring for the exact layout")
    parser.add_argument("--ped-name", required=True)
    parser.add_argument("--output-blend", required=True, help="Where to save the assembled .blend")
    parser.add_argument("--preview-png", default=None, help="Optional: also render a quick preview PNG")
    parser.add_argument("--body-donor-root", default=None,
                         help="Optional: directory of an already-exported-to-XML cross-ped "
                              "'body donor' (e.g. a nude reference ped chosen for its exact "
                              "bust/leg/hip measurements) whose uppr/lowr override this ped's "
                              "own layers -- see layered_ped_builder.resolve_body_donor_components")
    parser.add_argument("--body-donor-components", default="uppr,lowr",
                         help="Comma-separated component codes to take FROM the body donor "
                              "(default: uppr,lowr -- everything else always comes from this "
                              "ped's own normal layers, never the donor)")
    parser.add_argument("--extra-texture-roots", default=None,
                         help="Comma-separated extra directories to search for .dds textures "
                              "(e.g. a separate skin-texture-only folder not nested under "
                              "--ped-root or --body-donor-root) -- each gets its own additive "
                              "relink_external_textures() pass")
    parser.add_argument("--generic-skin-root", default=None,
                         help="Optional: directory of standard per-ethnicity skin textures "
                              "(mp_fm_skin_f_{up,lo}_{ethnicity}.dds) to swap onto the body "
                              "donor's uppr/lowr, decoupling the borrowed body SHAPE from the "
                              "donor's own bespoke skin TONE -- makes the donor reusable as a "
                              "generic nude base. Requires --body-donor-root.")
    parser.add_argument("--generic-skin-ethnicity", default="whi",
                         help="Ethnicity code for --generic-skin-root (ara/bla/chi/lat/pak/whi, "
                              "default: whi)")
    return parser.parse_args(raw_args)


def build_layered_ped(template_path: str, ped_root: str, ped_name: str,
                       body_donor_root: str | None = None,
                       body_donor_components: tuple[str, ...] = ("uppr", "lowr"),
                       extra_texture_roots: tuple[str, ...] = (),
                       generic_skin_root: str | None = None,
                       generic_skin_ethnicity: str = "whi") -> dict:
    """The bpy-dependent orchestration: resolve layers, import, rig, relink
    textures. Returns a stats dict for the caller to report/log. Split out
    from main() so a test can call it with a fake bpy and assert on the
    call sequence without needing a real Blender process.

    `body_donor_root`, when given, is a directory of already-exported
    CodeWalker-XML files (.ydd.xml) for a DIFFERENT ped chosen purely for
    its body-shape geometry (bust/legs/hips) -- see
    layered_ped_builder.resolve_layered_components_with_body_donor() for
    the full "by elimination" rule (only `body_donor_components` codes are
    taken from it, everything else still comes from this ped's own layers).
    """
    if body_donor_root:
        resolved = resolve_layered_components_with_body_donor(
            Path(ped_root), ped_name, Path(body_donor_root),
            allowed_donor_components=tuple(body_donor_components),
            extensions=(".ydd.xml",),
        )
    else:
        resolved = resolve_layered_components(Path(ped_root), ped_name,
                                               extensions=(".ydd.xml",))
    stats = {"resolved_components": {comp: str(p) for comp, p in resolved.items()}}

    bpy.ops.wm.open_mainfile(filepath=template_path)
    for module in ("bl_ext.user_default.sollumz", "sollumz"):
        try:
            bpy.ops.preferences.addon_enable(module=module)
            break
        except Exception:
            continue

    armature = bre.find_reference_armature(bpy.data.objects)
    stats["armature"] = armature.name if armature else None
    if armature is None:
        stats["error"] = "Template lacks a skeleton armature."
        return stats

    before_objs = set(bpy.data.objects)
    failed_imports = []
    new_meshes_by_component: dict[str, list] = {}
    for component, path in resolved.items():
        before_this_import = set(bpy.data.objects)
        try:
            bpy.ops.sollumz.import_assets(directory=str(path.parent) + "/",
                                           files=[{"name": path.name}])
        except Exception as exc:
            failed_imports.append((component, str(exc)))
            continue
        new_meshes_by_component[component] = [
            o for o in (set(bpy.data.objects) - before_this_import) if o.type == 'MESH'
        ]
    stats["failed_imports"] = failed_imports

    new_meshes = [o for o in (set(bpy.data.objects) - before_objs) if o.type == 'MESH']
    stats["imported_mesh_count"] = len(new_meshes)

    rigged = bre.rig_unskinned_body_components(armature, new_meshes)
    stats["rigged_count"] = len(rigged)

    relink_result = bre.relink_external_textures(ped_root)
    extra_roots = list(extra_texture_roots)
    if body_donor_root:
        extra_roots.append(body_donor_root)
    for root in extra_roots:
        # Each extra root lives in a SEPARATE directory tree (not nested
        # under ped_root) -- an additive pass picks up whatever earlier
        # passes didn't find. relink_external_textures() re-walks and
        # re-matches by basename each call, so calling it repeatedly is
        # safe (harmless redundant work for already-relinked images,
        # genuinely new matches for this root's own textures).
        pass_result = bre.relink_external_textures(root)
        relink_result["relinked"] = list(set(relink_result["relinked"]) | set(pass_result["relinked"]))
        relink_result["not_found"] = [n for n in relink_result["not_found"] if n in pass_result["not_found"]]
    stats["textures_relinked"] = len(relink_result["relinked"])
    stats["textures_not_found"] = relink_result["not_found"]

    if generic_skin_root:
        # Decouples the body donor's borrowed SHAPE from its own bespoke
        # skin TONE -- see blender_rig_export.apply_generic_skin_textures()
        # for the full rationale (makes the donor reusable as a generic
        # nude base across different peds, not pinned to one donor's
        # identity texture). Only meshes actually resolved to "uppr"/"lowr"
        # for THIS build are eligible -- if the donor didn't win those
        # codes (e.g. body_donor_root wasn't given), there's nothing to
        # swap and this is silently a no-op.
        body_objects = {}
        for component in ("uppr", "lowr"):
            meshes = new_meshes_by_component.get(component, [])
            if meshes:
                body_objects[component] = meshes[0]
        skin_swap_result = bre.apply_generic_skin_textures(
            body_objects, generic_skin_root, ethnicity=generic_skin_ethnicity)
        stats["generic_skin_swapped"] = skin_swap_result["swapped"]
        stats["generic_skin_not_found"] = skin_swap_result["not_found"]

    # Hide the template's own original body so only the new components show.
    #
    # PITFALL found 2026-10-12 while investigating a visually "doubled"
    # body in templates/template_mp_f_freemode_01_nude_base.blend: TWO
    # fixes were tried and both failed before this one.
    #   1. Iterating `armature.children` (Outliner parent-child hierarchy)
    #      missed meshes bound via an Armature MODIFIER with no Object
    #      parenting.
    #   2. Checking "has an Armature modifier pointing at `armature`, OR
    #      is a child of `armature`" STILL missed them: inspection showed
    #      templates/template_female_a_f_y_business_02.blend's own
    #      pre-existing body meshes (from earlier session work, baked
    #      permanently into that .blend) sit under a COMPLETELY SEPARATE,
    #      disconnected parent chain (hash_X.model -> an Empty -> a
    #      DIFFERENT empty object, "a_f_y_business_02.001" -- note the
    #      ".001", not the real armature) with NO modifiers at all. They
    #      just happen to visually overlap because they're the same ped's
    #      geometry at the same position -- there is no clean programmatic
    #      "skinned to our armature" relationship to detect at all.
    #   Real fix: this function's contract is that `template_path` ONLY
    #   ever contributes a skeleton, nothing else -- so ANY mesh/empty that
    #   existed in the scene BEFORE this run's own imports (tracked by
    #   `before_objs`, already computed above) is leftover clutter and must
    #   always be hidden, full stop, regardless of how (or whether) it's
    #   bound to anything.
    for obj in before_objs:
        if obj.type in ('MESH', 'EMPTY'):
            obj.hide_render = True
            obj.hide_set(True)
    # Hide known floor/backdrop junk (flat, large-footprint meshes -- found
    # 2026-10-12 occluding the upper body in a real render, see
    # docs/architecture-decisions.md).
    for obj in list(bpy.data.objects):
        if obj.type != 'MESH' or obj in new_meshes:
            continue
        dims = obj.dimensions
        if dims.z < 0.3 and (dims.x > 2.0 or dims.y > 2.0):
            obj.hide_render = True
            obj.hide_set(True)

    # Hide stray duplicate ARMATURE objects -- found 2026-10-12: the user
    # reported the skeleton "didn't look right" when inspecting
    # templates/template_nude_generic.blend interactively in Blender.
    # Root cause: some resolved components embed their OWN trivial/
    # placeholder skeleton in their .ydd.xml (the same ones that produce
    # the "SKEL_ROOT.001"/".002" floor-junk meshes just hidden above), and
    # Sollumz's importer creates a brand-new Armature object for each one
    # rather than reusing our existing `armature`. Those duplicate
    # skeletons deform nothing visible (their only mesh is the floor junk,
    # already hidden) but the ARMATURE OBJECTS THEMSELVES were never
    # hidden, so Blender drew THREE full 104-bone skeletons overlapping at
    # the same position -- confirmed by inspecting the real scene (3
    # armature objects, all with hide_render=False, only one of which
    # -- `armature` itself -- actually deforms a visible mesh). Any
    # Armature object other than our own working `armature` is always
    # junk by this function's contract (same reasoning as the mesh/empty
    # hide step above) and must be hidden too.
    for obj in list(bpy.data.objects):
        if obj.type == 'ARMATURE' and obj is not armature:
            obj.hide_render = True
            obj.hide_set(True)

    stats["new_meshes"] = new_meshes
    return stats


def _frame_camera_on(objects, lens=35.0):
    import mathutils
    all_corners = []
    for o in objects:
        if o.hide_render:
            continue
        all_corners.extend(o.matrix_world @ mathutils.Vector(c) for c in o.bound_box)
    if not all_corners:
        center, height = mathutils.Vector((0, 0, 1)), 1.8
    else:
        xs = [c[0] for c in all_corners]
        ys = [c[1] for c in all_corners]
        zs = [c[2] for c in all_corners]
        center = mathutils.Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
        height = max(zs) - min(zs)

    cam_data = bpy.data.cameras.new("preview_cam")
    cam_data.lens = lens
    cam_obj = bpy.data.objects.new("preview_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    dist = max(height * 2.0, 2.0)
    cam_obj.location = (center.x, center.y - dist, center.z)
    cam_obj.rotation_euler = (center - cam_obj.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = cam_obj


def render_preview(new_meshes, out_png: str):
    import mathutils  # noqa: F401  (re-imported for clarity at call site)
    if not any(o.type == 'LIGHT' for o in bpy.data.objects):
        light_data = bpy.data.lights.new("preview_light", type='SUN')
        light_data.energy = 3.0
        light_obj = bpy.data.objects.new("preview_light", light_data)
        bpy.context.scene.collection.objects.link(light_obj)
        light_obj.rotation_euler = (0.9, 0.0, 0.6)
    if bpy.context.scene.world is None:
        # A scene built from bpy.ops.wm.read_factory_settings(use_empty=True)
        # (e.g. a fresh native-skeleton template built straight from a
        # donor's own .yft, with no pre-existing business_02-style .blend
        # to inherit a World from) has NO World datablock at all -- found
        # 2026-10-12 as a real crash ('NoneType' object has no attribute
        # 'use_nodes') when building templates/template_nude_generic.blend
        # from templates/template_beachgirl5_native.blend for the first
        # time. Create one on demand rather than assuming it always exists.
        bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.world.use_nodes = True
    bg = bpy.context.scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.08, 0.08, 0.09, 1.0)
        bg.inputs[1].default_value = 1.0

    _frame_camera_on(new_meshes)

    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.render.resolution_x = 900
    bpy.context.scene.render.resolution_y = 1200
    bpy.context.scene.cycles.samples = 64
    bpy.context.scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)


def main():
    args = parse_args()
    donor_components = tuple(c.strip() for c in args.body_donor_components.split(",") if c.strip())
    extra_roots = tuple(r.strip() for r in args.extra_texture_roots.split(",") if r.strip()) \
        if args.extra_texture_roots else ()
    stats = build_layered_ped(args.template, args.ped_root, args.ped_name,
                               body_donor_root=args.body_donor_root,
                               body_donor_components=donor_components,
                               extra_texture_roots=extra_roots,
                               generic_skin_root=args.generic_skin_root,
                               generic_skin_ethnicity=args.generic_skin_ethnicity)

    print("[INFO] Resolved components:")
    for comp, path in stats.get("resolved_components", {}).items():
        print(f"  - {comp}: {path}")
    if stats.get("error"):
        print(f"[FAIL] {stats['error']}")
        sys.exit(1)
    print(f"[INFO] armature={stats['armature']} imported={stats['imported_mesh_count']} "
          f"rigged={stats['rigged_count']} textures_relinked={stats['textures_relinked']}")
    if stats["textures_not_found"]:
        print(f"[WARN] {len(stats['textures_not_found'])} textures not found in the asset set: "
              f"{stats['textures_not_found']}")
    if "generic_skin_swapped" in stats:
        print(f"[INFO] generic skin swapped for: {[c for c, _, _ in stats['generic_skin_swapped']]}")
        if stats["generic_skin_not_found"]:
            print(f"[WARN] generic skin texture not found for: {stats['generic_skin_not_found']}")
    for component, err in stats["failed_imports"]:
        print(f"[FAIL] import of component '{component}' failed: {err}")

    Path(args.output_blend).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args.output_blend)
    print(f"[OK] Saved {args.output_blend}")

    if args.preview_png:
        Path(args.preview_png).parent.mkdir(parents=True, exist_ok=True)
        render_preview(stats["new_meshes"], args.preview_png)
        print(f"[OK] Rendered preview to {args.preview_png}")


if __name__ == "__main__":
    main()
