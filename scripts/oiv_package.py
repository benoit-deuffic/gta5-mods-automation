"""Wrap a packaged streamed ped into an OpenIV package (.oiv).

    python scripts/oiv_package.py --ped-dir dist --name Camille --out dist [--icon preview.png]

Input: the layout `ped_package.py` writes, `<ped-dir>/<name>.yft`,
`<name>.ymt` and `<name>/` (one .ydd/.ytd per drawable/texture). Output:
`<out>/<name>.oiv`, a zip with `assembly.xml`, `icon.png` and `content/`.
Format: OpenIV-Team/OpenIV-PackageFormat specification/versions/2.2.md
(description = CDATA, icon exactly 128x128, nested <archive> paths relative
to the parent archive).

Install target = where PedSelector streams its peds (read from the user's
installed pack and from PedSelector.oiv): `update\\x64\\dlcpacks\\pedselector\\
dlc.rpf` > `peds.rpf` > `<name>.yft`, `<name>.ymt`, `<name>\\*`. The files are
added into those EXISTING archives through nested `<archive>` elements with
createIfNotExist="False": PedSelector must already be installed (it also
registers the pack in dlclist.xml), the package never creates a half pack.
Re-installing replaces the files (OpenIV `<add>` overwrites).

Not covered (an .oiv only touches the game folder): the ap_m.xml entry in
Documents/Rockstar Games/GTA V/UserMods -- PedSelector's UI or
xml_manifest_updater.py.
"""
from __future__ import annotations

import argparse
import struct
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

DLC_RPF = "update\\x64\\dlcpacks\\pedselector\\dlc.rpf"
PEDS_RPF = "peds.rpf"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_SIZE = 128  # OIV 2.2 spec: "Must be exactly 128x128 (px) size"
_CDATA_SLOT = "@@OIV_DESCRIPTION_CDATA@@"
# Stable namespace: the same ped name always gets the same package id, so
# OpenIV sees a re-generated package as the same package.
_ID_NAMESPACE = uuid.UUID("6f1c2a52-6a4e-4d2b-9a77-3c5d0e1f8b10")


class OivError(Exception):
    pass


def _solid_png(size: int = 128, rgb: tuple[int, int, int] = (0x3B, 0x59, 0x98)) -> bytes:
    """Plain PNG (stdlib only) used when no icon is given."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    row = b"\x00" + bytes(rgb) * size
    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def ped_files(ped_dir: Path, name: str) -> list[Path]:
    """The ped's files, relative to ped_dir; refuses an incomplete ped."""
    missing = [p for p in (f"{name}.yft", f"{name}.ymt") if not (ped_dir / p).is_file()]
    folder = ped_dir / name
    drawables = sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
    if not any(p.suffix == ".ydd" for p in drawables):
        missing.append(f"{name}/*.ydd")
    if missing:
        raise OivError(f"incomplete ped in {ped_dir}: missing {missing}")
    return [Path(f"{name}.yft"), Path(f"{name}.ymt")] + [p.relative_to(ped_dir) for p in drawables]


def assembly_xml(name: str, files: list[Path]) -> bytes:
    package = ET.Element("package", version="2.2", id="{%s}" % str(uuid.uuid5(_ID_NAMESPACE, name)).upper(),
                         target="Five")
    meta = ET.SubElement(package, "metadata")
    ET.SubElement(meta, "name").text = name
    version = ET.SubElement(meta, "version")
    ET.SubElement(version, "major").text = "1"
    ET.SubElement(version, "minor").text = "0"
    ET.SubElement(version, "tag").text = "STABLE"
    ET.SubElement(ET.SubElement(meta, "author"), "displayName").text = "gta5-mods-automation"
    # The spec types description as CData: OpenIV rejects plain text there
    # ("metadata information is missed or corrupted"). ElementTree can't
    # write CDATA, so a placeholder is swapped after serialization.
    ET.SubElement(meta, "description").text = _CDATA_SLOT
    description = (
        f"Ped {name} (streamed) for PedSelector.\n"
        "Requires PedSelector already installed (dlcpacks/pedselector). Install into the mods folder.\n"
        f"Then register {name} in UserMods/ap_m.xml (PedSelector, Streamed = true).")
    colors = ET.SubElement(package, "colors")
    ET.SubElement(colors, "headerBackground", useBlackTextColor="False").text = "$FF004040"
    ET.SubElement(colors, "iconBackground").text = "$FF3B5998"
    content = ET.SubElement(package, "content")
    dlc = ET.SubElement(content, "archive", path=DLC_RPF, createIfNotExist="False", type="RPF7")
    peds = ET.SubElement(dlc, "archive", path=PEDS_RPF, createIfNotExist="False", type="RPF7")
    for rel in files:
        ET.SubElement(peds, "add", source=rel.as_posix()).text = "\\".join(rel.parts)
    ET.indent(package, space="\t")
    cdata = "<![CDATA[" + description.replace("]]>", "]]]]><![CDATA[>") + "]]>"
    body = ET.tostring(package, encoding="unicode").replace(_CDATA_SLOT, cdata, 1)
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + body).encode("utf-8")


def build_oiv(ped_dir: Path, name: str, out_dir: Path, icon: Path | None = None) -> Path:
    files = ped_files(ped_dir, name)
    if icon is not None:
        icon_bytes = icon.read_bytes()
        if not icon_bytes.startswith(PNG_SIGNATURE):
            raise OivError(f"icon is not a PNG: {icon}")
        size = struct.unpack(">II", icon_bytes[16:24])  # IHDR width, height
        if size != (ICON_SIZE, ICON_SIZE):
            raise OivError(f"icon must be exactly {ICON_SIZE}x{ICON_SIZE} px (OIV spec), got {size[0]}x{size[1]}: {icon}")
    else:
        icon_bytes = _solid_png()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.oiv"
    tmp = out.with_suffix(".oiv.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("assembly.xml", assembly_xml(name, files))
        z.writestr("icon.png", icon_bytes)
        for rel in files:
            z.write(ped_dir / rel, f"content/{rel.as_posix()}")
    tmp.replace(out)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Wrap a packaged streamed ped into an OpenIV .oiv (PedSelector)")
    parser.add_argument("--ped-dir", required=True, type=Path, help="Directory with <name>.yft, <name>.ymt, <name>/")
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--icon", type=Path, default=None, help="PNG shown by OpenIV (default: plain colour)")
    args = parser.parse_args(argv)
    try:
        out = build_oiv(args.ped_dir, args.name, args.out, args.icon)
    except OivError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
