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
the reshape is replayed on it as Blender baked it (reshape_ped_xml: the
skin components on every LOD -- Blender only ever imported the High one --
normals and tangents carried through the deformation's Jacobian; every other
component follows the skin under it; bounds grown), then converted back with
codewalker-cli. Nothing
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

from body_morphology import (  # noqa: E402
    SKIN_COMPONENTS, Deformation, carry_normal, carry_tangent, deform_with_jacobians, follow_skin, region_weight,
)
from codewalker_convert import convert_xml_to_binary, export_binary_to_xml  # noqa: E402
from layered_ped_builder import bone_group_remap, skeleton_bone_names  # noqa: E402


class PackageError(Exception):
    pass


# Tokens per vertex element in a CodeWalker-XML <VertexBuffer><Data2> line.
LAYOUT_SIZES = {"Position": 3, "BlendWeights": 4, "BlendIndices": 4, "Normal": 3,
                "Colour0": 4, "Colour1": 4, "Tangent": 4, "Binormal": 4,
                **{f"TexCoord{i}": 2 for i in range(8)}}
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


def _set_xyz(el, xyz):
    for axis, v in zip("xyz", xyz):
        el.set(axis, _fmt(v))


def _xyz(el):
    return tuple(float(el.get(a)) for a in "xyz")


class _Geometry:
    """One <Geometries><Item> of a parsed .ydd.xml: positions (and normals,
    tangents) are edited in memory, written back by flush() -- only the
    vertices that ended up moved, so the others stay byte-identical."""

    def __init__(self, element, skeleton_names, declared_count, high):
        self.element, self.high = element, high
        vb = element.find("VertexBuffer")
        self.layout = [el.tag for el in vb.find("Layout")]
        unknown = [t for t in self.layout if t not in LAYOUT_SIZES]
        if unknown:
            raise PackageError(f"unknown vertex elements {unknown}")
        self.offsets, at = {}, 0
        for tag in self.layout:
            self.offsets[tag] = at
            at += LAYOUT_SIZES[tag]
        if "Position" not in self.offsets:
            raise PackageError("geometry without Position")
        self.data = vb.find("Data2") if vb.find("Data2") is not None else vb.find("Data")
        self.lines = self.data.text.split("\n")
        self.rows = [(k, line.split()) for k, line in enumerate(self.lines) if line.strip()]
        if any(len(tokens) != at for _k, tokens in self.rows):
            raise PackageError(f"vertex line length != layout {self.layout} ({at} values)")
        po = self.offsets["Position"]
        self.original = [tuple(float(t) for t in tokens[po:po + 3]) for _k, tokens in self.rows]
        self.points = list(self.original)
        self.normals = self._vectors("Normal", 3)
        self.tangents = self._vectors("Tangent", 3)
        self.influences = None
        if "BlendWeights" in self.offsets and "BlendIndices" in self.offsets:
            bone_ids = [int(x) for x in (element.findtext("BoneIDs") or "").replace(",", " ").split()]
            palette = _palette_names(skeleton_names, declared_count, bone_ids)
            wo, io = self.offsets["BlendWeights"], self.offsets["BlendIndices"]
            self.influences = [[(palette[int(tokens[io + n])], int(tokens[wo + n]) / 255.0) for n in range(4)
                                if int(tokens[wo + n])] for _k, tokens in self.rows]

    def _vectors(self, tag, size):
        if tag not in self.offsets:
            return None
        o = self.offsets[tag]
        return [[float(t) for t in tokens[o:o + size]] for _k, tokens in self.rows]

    def deform(self, deformation):
        """Skin: the analytic reshape, normals/tangents through its Jacobian."""
        if self.influences is None:
            raise PackageError("skin geometry without BlendWeights/BlendIndices: can't weight the reshape")
        weights = [region_weight(inf, deformation.prefixes) for inf in self.influences]
        moved, jacobians = deform_with_jacobians(deformation, self.points, weights)
        for v, jac in jacobians.items():
            if self.normals is not None:
                self.normals[v] = list(carry_normal(jac, self.normals[v]))
            if self.tangents is not None:
                self.tangents[v] = list(carry_tangent(jac, self.tangents[v]))
        self.points = moved

    def follow(self, skin_from, skin_to):
        """Non-skin: translate with the skin under it (body_morphology.follow_skin)."""
        self.points = follow_skin(self.points, skin_from, skin_to)

    def moved_count(self):
        return sum(1 for a, b in zip(self.original, self.points)
                   if max(abs(x - y) for x, y in zip(a, b)) > MOVED_EPSILON)

    def flush(self):
        """Write moved vertices back; grow the geometry box. Returns the
        growth (lo, hi) per axis, or None if nothing moved."""
        po = self.offsets["Position"]
        count = 0
        for v, ((k, tokens), old, new) in enumerate(zip(self.rows, self.original, self.points)):
            if max(abs(x - y) for x, y in zip(old, new)) <= MOVED_EPSILON:
                continue
            count += 1
            tokens[po:po + 3] = [_fmt(c) for c in new]
            for tag, vectors in (("Normal", self.normals), ("Tangent", self.tangents)):
                if vectors is not None:
                    o = self.offsets[tag]
                    tokens[o:o + 3] = [_fmt(c) for c in vectors[v]]
            indent = self.lines[k][:len(self.lines[k]) - len(self.lines[k].lstrip())]
            self.lines[k] = indent + "   ".join(" ".join(tokens[self.offsets[t]:self.offsets[t] + LAYOUT_SIZES[t]])
                                                for t in self.layout)
        if not count:
            return None
        self.data.text = "\n".join(self.lines)
        lo = tuple(min(p[a] for p in self.points) for a in range(3))
        hi = tuple(max(p[a] for p in self.points) for a in range(3))
        box_lo, box_hi = self.element.find("BoundingBoxMin"), self.element.find("BoundingBoxMax")
        old_lo, old_hi = _xyz(box_lo), _xyz(box_hi)
        _set_xyz(box_lo, tuple(map(min, old_lo, lo)))
        _set_xyz(box_hi, tuple(map(max, old_hi, hi)))
        return ([max(0.0, a - b) for a, b in zip(old_lo, lo)], [max(0.0, b - a) for a, b in zip(old_hi, hi)])


_LODS = ("DrawableModelsHigh", "DrawableModelsMedium", "DrawableModelsLow", "DrawableModelsVeryLow")


def _geometries(root, skeleton_names):
    """[(drawable element, [_Geometry])] of a parsed .ydd.xml, every LOD."""
    out = []
    for drawable in root.findall("Item"):
        geoms = []
        for lod in _LODS:
            for model in drawable.findall(f"{lod}/Item"):
                declared = int(model.find("Unknown1").get("value"))
                geoms += [_Geometry(g, skeleton_names, declared, lod == "DrawableModelsHigh")
                          for g in model.findall("Geometries/Item")]
        out.append((drawable, geoms))
    return out


def _flush(drawables) -> int:
    """Write every geometry back; the drawable's own bounds are NOT in the
    geometries' frame (CatalinaNude lowr: drawable z 0.03..1.10, geometry
    -0.94..0.08): they grow by as much as the geometries' did (never
    shrink -- a too-large bound only costs culling, a too-small one pops)."""
    total = 0
    for drawable, geoms in drawables:
        grow_lo, grow_hi = [0.0] * 3, [0.0] * 3
        for g in geoms:
            total += g.moved_count()
            growth = g.flush()
            if growth:
                grow_lo = list(map(max, grow_lo, growth[0]))
                grow_hi = list(map(max, grow_hi, growth[1]))
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


def reshape_ped_xml(roots: dict, deformations, skeleton_names) -> dict:
    """Replay `deformations` on a ped's parsed .ydd.xml roots ({component:
    root}), in place, exactly as body_morphology._write baked them in
    Blender: for each deformation in order, the SKIN_COMPONENTS get the
    analytic reshape (every LOD), then every other component follows the
    skin's High-LOD displacement (the only LOD Blender imported). Returns
    {component: moved vertex count}."""
    parsed = {c: _geometries(root, skeleton_names) for c, root in roots.items()}
    skin = [c for c in parsed if c in SKIN_COMPONENTS]
    for deformation in deformations:
        skin_from, skin_to = [], []
        for c in skin:
            for _drawable, geoms in parsed[c]:
                for g in geoms:
                    before = g.points
                    g.deform(deformation)
                    if g.high:
                        skin_from += before
                        skin_to += g.points
        for c in parsed:
            if c not in skin:
                for _drawable, geoms in parsed[c]:
                    for g in geoms:
                        g.follow(skin_from, skin_to)
    return {c: _flush(drawables) for c, drawables in parsed.items()}


def _texture_files(root: ET.Element) -> list[str]:
    """Embedded texture .dds files a .ydd.xml needs next to it to convert."""
    return [name for name in (el.findtext("FileName") for el in root.iter("Item")
                              if el.find("Usage") is not None) if name]


def _export(original: Path, work: Path) -> Path:
    """Export ONE original binary .ydd to CodeWalker-XML in its own folder
    (with its own embedded .dds -- see module doc). Returns the .ydd.xml."""
    src = work / original.name[:-len(".ydd")] / "xml"
    exported = export_binary_to_xml(original, src)
    xml_path = src / f"{original.name}.xml"
    if exported.status != "converted" or not xml_path.is_file():
        raise PackageError(f"codewalker-cli export of {original} failed: {exported.status} {exported.message}")
    return xml_path


def _convert(xml_path: Path, tree: ET.ElementTree) -> Path:
    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
    src = xml_path.parent
    missing = [t for t in _texture_files(tree.getroot()) if not (src / t).is_file()]
    if missing:
        raise PackageError(f"{xml_path.name}: embedded textures not exported: {missing}")
    stem = xml_path.name[:-len(".ydd.xml")]
    result = convert_xml_to_binary(src, src.parent / "bin", stem, extensions=(".ydd",))
    binary = src.parent / "bin" / f"{stem}.ydd"
    if result.status != "converted" or not binary.is_file():
        raise PackageError(f"codewalker-cli conversion of {xml_path.name} failed: {result.status} {result.message}")
    return binary


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
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out, prefix=f".{name}.") as tmp:
        work = Path(tmp)
        # component -> (tree, exported .ydd.xml or None if not one of <ped>'s own files)
        loaded = {}
        for component, xml in sorted(spec["components"].items()):
            xml = Path(xml)
            if not xml.name.endswith(".ydd.xml") or not deformations:
                continue
            if os.path.abspath(xml.parent) != os.path.abspath(top_tier):
                # A lower tier or a body donor has no slot in the shipped
                # folder -- fine as long as it doesn't move (positions don't
                # depend on the flat folder's texture collisions).
                loaded[component] = (ET.parse(xml), None)
                continue
            original = originals["dir"] / xml.name[:-len(".xml")]
            if not original.is_file():
                raise PackageError(f"no original binary {original} for {xml.name}")
            exported = _export(original, work)
            loaded[component] = (ET.parse(exported), exported)
        moved = reshape_ped_xml({c: tree.getroot() for c, (tree, _x) in loaded.items()},
                                deformations, skeleton_names)
        reshaped, unchanged = {}, sorted(c for c, n in moved.items() if not n)
        for component, count in sorted(moved.items()):
            if not count:
                continue
            tree, exported = loaded[component]
            if exported is None:
                raise PackageError(f"{component} ({spec['components'][component]}) is reshaped but "
                                   f"isn't one of {ped}'s own files")
            reshaped[exported.name[:-len(".xml")]] = (count, _convert(exported, tree))

        stage = work / "stage"
        shutil.copytree(originals["dir"], stage / name)
        shutil.copy2(originals["yft"], stage / f"{name}.yft")
        shutil.copy2(originals["ymt"], stage / f"{name}.ymt")
        for file_name, (_count, binary) in reshaped.items():
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
