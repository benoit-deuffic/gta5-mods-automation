"""Ship a reshaped ped as AddonPeds/PedSelector streamed-ped files.

    python scripts/ped_package.py --deformation build/x.deform.json \
        --source import --name CatalinaNude_80A --out dist

`--deformation` is written by `import_layered_ped.py --deformation-out`: the
fitted reshapes (body_morphology.Deformation) and the component files they
were measured on. Output, under --out:

    <name>.yft  <name>.ymt  <name>/   (the layout ap_m.xml / PedSelector stream)

Everything is copied byte for byte from the user's own original binaries in
`<source>/<ped>.yft|.ymt|<ped>/` -- the .ymt holds no name, so renaming the
ped is renaming the files. Only the .ydd files the reshape actually moves are
regenerated: each original binary is exported to CodeWalker-XML on its own,
the reshape is replayed on it (every LOD -- Blender only ever imported the
High one -- normals and tangents carried through the deformation's
Jacobian, bounds grown), then converted back with codewalker-cli. Nothing
goes through a Sollumz export: names, shaders, embedded textures and
skinning stay the original ones.

Why not reuse the import_xml/ tree: it is one flat folder per ped, and
sibling .ydd files embed DIFFERENT textures under the SAME name (CatalinaNude
lowr_000/001/002 all embed lowr_normal_015, 512 DXT1 vs 1024 DXT5): the
folder keeps only the last one exported, so rebuilding from it silently
swaps embedded textures.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from body_morphology import Deformation, region_weight  # noqa: E402
from codewalker_convert import convert_xml_to_binary, export_binary_to_xml  # noqa: E402
from layered_ped_builder import bone_group_remap, skeleton_bone_names  # noqa: E402


class PackageError(Exception):
    pass


# Tokens per vertex element in a CodeWalker-XML <VertexBuffer><Data2> line.
LAYOUT_SIZES = {"Position": 3, "BlendWeights": 4, "BlendIndices": 4, "Normal": 3,
                "Colour0": 4, "Colour1": 4, "Tangent": 4, "Binormal": 4,
                **{f"TexCoord{i}": 2 for i in range(8)}}
FD_STEP = 1e-4        # m, finite-difference step of the Jacobian
MOVED_EPSILON = 1e-7  # m, below that a vertex is left byte-identical
PED_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _fmt(v: float) -> str:
    return f"{v:.7g}"


def _palette_names(skeleton_names, declared_count, bone_ids):
    """Bone name of each skin-palette slot of one geometry: <BoneIDs> maps
    slot -> skeleton index; a palette built for another skeleton size is
    renamed exactly as the Blender import was (bone_group_remap)."""
    remap = bone_group_remap(skeleton_names, declared_count)
    names = [remap.get(n, n) for n in skeleton_names]
    return [names[i] for i in bone_ids] if bone_ids else names


def _transform(deformations, weight_lists, points):
    for d, w in zip(deformations, weight_lists):
        points = d.apply(points, w)
    return points


def _inverse_transpose(j):
    """Adjugate-transpose (= det * J^-T) of a 3x3 row-major Jacobian: enough
    to carry a normal, which is renormalized anyway."""
    (a, b, c), (d, e, f), (g, h, i) = j
    return ((e * i - f * h, f * g - d * i, d * h - e * g),
            (c * h - b * i, a * i - c * g, b * g - a * h),
            (b * f - c * e, c * d - a * f, a * e - b * d))


def _mul(m, v):
    return tuple(sum(m[r][k] * v[k] for k in range(3)) for r in range(3))


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 0 else tuple(v)


def _geometry_deform(geometry, deformations, skeleton_names, declared_count):
    """Deform one <Geometries><Item> in place. Returns (moved count,
    new bbox min, new bbox max) of its positions."""
    vb = geometry.find("VertexBuffer")
    layout = [el.tag for el in vb.find("Layout")]
    unknown = [t for t in layout if t not in LAYOUT_SIZES]
    if unknown:
        raise PackageError(f"unknown vertex elements {unknown}")
    offsets, at = {}, 0
    for tag in layout:
        offsets[tag] = at
        at += LAYOUT_SIZES[tag]
    data = vb.find("Data2") if vb.find("Data2") is not None else vb.find("Data")
    lines = data.text.split("\n")
    rows = [(k, line.split()) for k, line in enumerate(lines) if line.strip()]
    if any(len(tokens) != at for _k, tokens in rows):
        raise PackageError(f"vertex line length != layout {layout} ({at} values)")
    for needed in ("Position", "BlendWeights", "BlendIndices"):
        if needed not in offsets:
            raise PackageError(f"geometry without {needed}: can't weight the reshape")

    bone_ids = [int(x) for x in (geometry.findtext("BoneIDs") or "").replace(",", " ").split()]
    palette = _palette_names(skeleton_names, declared_count, bone_ids)
    po, wo, io = offsets["Position"], offsets["BlendWeights"], offsets["BlendIndices"]
    points = [tuple(float(t) for t in tokens[po:po + 3]) for _k, tokens in rows]
    influences = [[(palette[int(tokens[io + n])], int(tokens[wo + n]) / 255.0) for n in range(4)
                   if int(tokens[wo + n])] for _k, tokens in rows]
    weight_lists = [[region_weight(inf, d.prefixes) for inf in influences] for d in deformations]

    moved = _transform(deformations, weight_lists, points)
    probes = [_transform(deformations, weight_lists,
                         [tuple(c + (FD_STEP if a == axis else 0.0) for a, c in enumerate(p)) for p in points])
              for axis in range(3)]
    count = 0
    for v, ((k, tokens), old, new) in enumerate(zip(rows, points, moved)):
        if max(abs(n - o) for n, o in zip(new, old)) <= MOVED_EPSILON:
            continue
        count += 1
        # column `axis` of J = d new / d axis
        jac = tuple(tuple((probes[axis][v][r] - new[r]) / FD_STEP for axis in range(3)) for r in range(3))
        tokens[po:po + 3] = [_fmt(c) for c in new]
        if "Normal" in offsets:
            no = offsets["Normal"]
            normal = _unit(_mul(_inverse_transpose(jac), [float(t) for t in tokens[no:no + 3]]))
            tokens[no:no + 3] = [_fmt(c) for c in normal]
        if "Tangent" in offsets:
            to = offsets["Tangent"]
            tangent = _unit(_mul(jac, [float(t) for t in tokens[to:to + 3]]))
            tokens[to:to + 3] = [_fmt(c) for c in tangent]
        indent = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
        lines[k] = indent + "   ".join(" ".join(tokens[offsets[t]:offsets[t] + LAYOUT_SIZES[t]]) for t in layout)
    if count:
        data.text = "\n".join(lines)
    lo = tuple(min(p[a] for p in moved) for a in range(3))
    hi = tuple(max(p[a] for p in moved) for a in range(3))
    return count, lo, hi


def _set_xyz(el, xyz):
    for axis, v in zip("xyz", xyz):
        el.set(axis, _fmt(v))


def _xyz(el):
    return tuple(float(el.get(a)) for a in "xyz")


def deform_drawable_dictionary(root: ET.Element, deformations, skeleton_names) -> int:
    """Replay `deformations` on every geometry of every LOD of every drawable
    of a parsed .ydd.xml, in place. Returns the number of moved vertices;
    bounds are only touched when something moved (grown, never shrunk --
    a too-large bound only costs culling, a too-small one pops). The
    drawable's own bounds are NOT in the geometries' frame (CatalinaNude
    lowr: drawable z 0.03..1.10, geometry -0.94..0.08): they only grow by
    as much as the geometries' bounds did."""
    total = 0
    for drawable in root.findall("Item"):
        grow_lo, grow_hi = [0.0] * 3, [0.0] * 3
        for lod in ("DrawableModelsHigh", "DrawableModelsMedium", "DrawableModelsLow", "DrawableModelsVeryLow"):
            for model in drawable.findall(f"{lod}/Item"):
                declared = int(model.find("Unknown1").get("value"))
                for geometry in model.findall("Geometries/Item"):
                    count, lo, hi = _geometry_deform(geometry, deformations, skeleton_names, declared)
                    if not count:
                        continue
                    total += count
                    box_lo, box_hi = geometry.find("BoundingBoxMin"), geometry.find("BoundingBoxMax")
                    old_lo, old_hi = _xyz(box_lo), _xyz(box_hi)
                    for a in range(3):
                        grow_lo[a] = max(grow_lo[a], old_lo[a] - lo[a])
                        grow_hi[a] = max(grow_hi[a], hi[a] - old_hi[a])
                    _set_xyz(box_lo, tuple(map(min, old_lo, lo)))
                    _set_xyz(box_hi, tuple(map(max, old_hi, hi)))
        if not any(grow_lo + grow_hi):
            continue
        box_lo, box_hi = drawable.find("BoundingBoxMin"), drawable.find("BoundingBoxMax")
        if box_lo is not None and box_hi is not None:
            _set_xyz(box_lo, tuple(v - g for v, g in zip(_xyz(box_lo), grow_lo)))
            _set_xyz(box_hi, tuple(v + g for v, g in zip(_xyz(box_hi), grow_hi)))
        radius = drawable.find("BoundingSphereRadius")
        if radius is not None:
            radius.set("value", _fmt(float(radius.get("value"))
                                     + math.hypot(*(max(a, b) for a, b in zip(grow_lo, grow_hi)))))
    return total


def _texture_files(root: ET.Element) -> list[str]:
    """Embedded texture .dds files a .ydd.xml needs next to it to convert."""
    return [name for name in (el.findtext("FileName") for el in root.iter("Item")
                              if el.find("Usage") is not None) if name]


def _reshaped_ydd(original: Path, deformations, skeleton_names, work: Path) -> tuple[int, Path | None]:
    """(moved vertices, regenerated binary .ydd or None if nothing moved)
    for the original binary `original`, exported alone (see module doc)."""
    stem = original.name[:-len(".ydd")]
    src = work / stem / "xml"
    exported = export_binary_to_xml(original, src)
    xml_path = src / f"{original.name}.xml"
    if exported.status != "converted" or not xml_path.is_file():
        raise PackageError(f"codewalker-cli export of {original} failed: {exported.status} {exported.message}")
    tree = ET.parse(xml_path)
    moved = deform_drawable_dictionary(tree.getroot(), deformations, skeleton_names)
    if not moved:
        return 0, None
    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
    missing = [t for t in _texture_files(tree.getroot()) if not (src / t).is_file()]
    if missing:
        raise PackageError(f"{original.name}: embedded textures not exported: {missing}")
    result = convert_xml_to_binary(src, work / stem / "bin", stem, extensions=(".ydd",))
    binary = work / stem / "bin" / f"{stem}.ydd"
    if result.status != "converted" or not binary.is_file():
        raise PackageError(f"codewalker-cli conversion of {xml_path.name} failed: {result.status} {result.message}")
    return moved, binary


def package_ped(deformation_json: Path, source: Path, name: str, out: Path) -> dict:
    if not PED_NAME_RE.match(name):
        raise PackageError(f"invalid ped name {name!r}: letters, digits and _ only")
    spec = json.loads(deformation_json.read_text())
    ped = spec["ped_name"]
    deformations = [Deformation.from_dict(d) for d in spec["deformations"]]
    originals = {"yft": source / f"{ped}.yft", "ymt": source / f"{ped}.ymt", "dir": source / ped}
    missing = [str(p) for p in originals.values() if not p.exists()]
    if missing:
        raise PackageError(f"original binaries missing: {missing}")
    targets = [out / f"{name}.yft", out / f"{name}.ymt", out / name]
    existing = [str(p) for p in targets if p.exists()]
    if existing:
        raise PackageError(f"output already exists, not overwriting: {existing}")
    if deformations and not spec.get("skeleton_yft"):
        raise PackageError("the build had no skeleton .yft: skin weights unknown, can't replay the reshape")
    skeleton_names = skeleton_bone_names(Path(spec["skeleton_yft"])) if deformations else []

    top_tier = Path(spec["ped_root"]) / ped
    reshaped, unchanged = {}, []
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out, prefix=f".{name}.") as tmp:
        work = Path(tmp)
        for component, xml in sorted(spec["components"].items()):
            xml = Path(xml)
            if not xml.name.endswith(".ydd.xml") or not deformations:
                continue
            # The shipped ped is <ped>'s own folder: a component fitted from
            # a lower tier or a body donor has no slot in it -- fine as long
            # as the reshape doesn't move it (positions don't depend on the
            # flat folder's texture collisions, its XML is enough to tell).
            if os.path.abspath(xml.parent) != os.path.abspath(top_tier):
                if deform_drawable_dictionary(ET.parse(xml).getroot(), deformations, skeleton_names):
                    raise PackageError(f"{component} ({xml}) is reshaped but isn't one of {ped}'s own files")
                unchanged.append(component)
                continue
            original = originals["dir"] / xml.name[:-len(".xml")]
            if not original.is_file():
                raise PackageError(f"no original binary {original} for {xml.name}")
            moved, binary = _reshaped_ydd(original, deformations, skeleton_names, work)
            if moved:
                reshaped[original.name] = (moved, binary)
            else:
                unchanged.append(component)

        stage = work / "stage"
        shutil.copytree(originals["dir"], stage / name)
        shutil.copy2(originals["yft"], stage / f"{name}.yft")
        shutil.copy2(originals["ymt"], stage / f"{name}.ymt")
        for file_name, (_moved, binary) in reshaped.items():
            shutil.copy2(binary, stage / name / file_name)
        for target in targets:
            os.replace(stage / target.name, target)
    return {"output": [str(t) for t in targets], "reshaped": {f: m for f, (m, _b) in reshaped.items()},
            "unchanged_components": unchanged}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Assemble a reshaped ped's streamed files (AddonPeds/PedSelector)")
    parser.add_argument("--deformation", required=True, type=Path,
                        help="JSON from import_layered_ped.py --deformation-out")
    parser.add_argument("--source", required=True, type=Path,
                        help="Directory with the original <ped>.yft, <ped>.ymt and <ped>/ binaries")
    parser.add_argument("--name", required=True, help="New ped name (files are renamed to it)")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = package_ped(args.deformation, args.source, args.name, args.out)
    except PackageError as exc:
        print(f"[FAIL] {exc}")
        return 1
    for file_name, moved in report["reshaped"].items():
        print(f"[OK] reshaped {file_name}: {moved} vertices (all LODs)")
    print(f"[INFO] unchanged components (original binaries): {report['unchanged_components']}")
    print(f"[OK] {', '.join(report['output'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
