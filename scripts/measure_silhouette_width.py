"""Measure a mesh's silhouette WIDTH at a given world-Z height band --
independent of vertex-group weight painting (which turned out to be an
UNRELIABLE calibration signal, see docs/architecture-decisions.md: a donor
mesh and the base mesh paint weights very differently -- e.g. the donor's
pelvis vertex group covered 813 verts spanning most of the thigh, the
base's covered only 58 verts right at the waistband edge -- so comparing
"bounding box of weighted vertices" between the two compares different
ANATOMICAL REGIONS, not the same measurement, and gave a result that
contradicted the visual reality (the donor LOOKING curvier).

This measures the real silhouette instead: take every vertex within
`half_band` of `target_z` (world space), compute the world-space X and Y
extent of just those vertices. This measures "how wide is the body at
this height", independent of which bone happens to be weighted there.

Run via:
    blender --background --python scripts/measure_silhouette_width.py -- \
        --blend <path> --mesh <object_name> --z <world_z> --half-band 0.05 \
        --out <json_path> --label <str>
"""
import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args():
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--z", type=float, required=True)
    parser.add_argument("--half-band", type=float, default=0.03)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", default="")
    return parser.parse_args(raw_args)


def measure_slice(mesh_obj, target_z, half_band):
    xs, ys = [], []
    for v in mesh_obj.data.vertices:
        world_co = mesh_obj.matrix_world @ v.co
        if abs(world_co.z - target_z) <= half_band:
            xs.append(world_co.x)
            ys.append(world_co.y)
    if not xs:
        return None
    return {"width_x": max(xs) - min(xs), "depth_y": max(ys) - min(ys), "slice_vert_count": len(xs)}


def main():
    args = parse_args()
    bpy.ops.wm.open_mainfile(filepath=args.blend)
    for module in ("bl_ext.user_default.sollumz", "sollumz"):
        try:
            bpy.ops.preferences.addon_enable(module=module)
            break
        except Exception:
            continue

    mesh_obj = bpy.data.objects.get(args.mesh)
    if mesh_obj is None:
        print(f"[FAIL] mesh object {args.mesh!r} not found in {args.blend}")
        sys.exit(1)

    result = measure_slice(mesh_obj, args.z, args.half_band)
    record = {"label": args.label, "blend": args.blend, "mesh": args.mesh,
              "z": args.z, "half_band": args.half_band, "result": result}
    print(f"[INFO] {args.label} {args.mesh} @ z={args.z}: {result}")

    out_path = Path(args.out)
    records = json.loads(out_path.read_text()) if out_path.exists() else []
    records.append(record)
    out_path.write_text(json.dumps(records, indent=2))
    print(f"[OK] wrote {out_path}")


if __name__ == "__main__":
    main()
