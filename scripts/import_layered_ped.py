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
from layered_ped_builder import resolve_layered_components  # noqa: E402


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
    return parser.parse_args(raw_args)


def build_layered_ped(template_path: str, ped_root: str, ped_name: str) -> dict:
    """The bpy-dependent orchestration: resolve layers, import, rig, relink
    textures. Returns a stats dict for the caller to report/log. Split out
    from main() so a test can call it with a fake bpy and assert on the
    call sequence without needing a real Blender process.
    """
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
    for component, path in resolved.items():
        try:
            bpy.ops.sollumz.import_assets(directory=str(path.parent) + "/",
                                           files=[{"name": path.name}])
        except Exception as exc:
            failed_imports.append((component, str(exc)))
    stats["failed_imports"] = failed_imports

    new_meshes = [o for o in (set(bpy.data.objects) - before_objs) if o.type == 'MESH']
    stats["imported_mesh_count"] = len(new_meshes)

    rigged = bre.rig_unskinned_body_components(armature, new_meshes)
    stats["rigged_count"] = len(rigged)

    relink_result = bre.relink_external_textures(ped_root)
    stats["textures_relinked"] = len(relink_result["relinked"])
    stats["textures_not_found"] = relink_result["not_found"]

    # Hide the template's own original body so only the new components show.
    for obj in list(armature.children):
        if obj not in new_meshes and obj.type in ('MESH', 'EMPTY'):
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
    stats = build_layered_ped(args.template, args.ped_root, args.ped_name)

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
