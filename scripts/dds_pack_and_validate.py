"""Standalone DDS packaging + CodeWalker round-trip validation step, run with
the sd-local venv's Python (has Pillow with DDS write support):

    ~/tools/sd-local/venv/bin/python scripts/dds_pack_and_validate.py \
        --source-png <reskin.png> --ytd-xml-dir <dir with the .ytd.xml + loose .dds> \
        --texture-name uppr_diff_000_a_whi --codewalker-cli <path> \
        --model-name a_f_y_business_02 --out-dir <dir>

Reads the original texture's <Format>/<Width>/<Height> from the existing
<model>.ytd.xml so the new DDS matches pixel format exactly (acceptance
criterion from the task: "save as DDS in the SAME pixel format the original
texture used"). Overwrites the loose DDS file in place (same filename) so
the existing .ytd.xml's <FileName> reference keeps resolving -- no XML
regeneration needed for a same-name texture swap.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

_FORMAT_TO_PIXEL_FORMAT = {
    "D3DFMT_DXT1": "DXT1",
    "D3DFMT_DXT3": "DXT3",
    "D3DFMT_DXT5": "DXT5",
}


def read_texture_format(ytd_xml_path: Path, texture_name: str) -> dict:
    """Parses the .ytd.xml by locating the <Item> block whose <FileName>
    matches texture_name(.dds), returning its Width/Height/Format/MipLevels.
    Simple regex-based scan (the file is large; avoids a full XML parse of
    the whole embedded-texture blob) -- good enough for this read-only check.
    """
    text = ytd_xml_path.read_text(errors="ignore")
    pattern = re.compile(
        r"<Width value=\"(\d+)\" />\s*<Height value=\"(\d+)\" />"
        r"\s*<MipLevels value=\"(\d+)\" />\s*<Format>([^<]+)</Format>\s*<FileName>([^<]+)</FileName>",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        width, height, miplevels, fmt, filename = m.groups()
        if filename.strip() in (f"{texture_name}.dds", texture_name):
            return {
                "width": int(width), "height": int(height),
                "mip_levels": int(miplevels), "format": fmt.strip(),
                "filename": filename.strip(),
            }
    raise RuntimeError(f"Texture {texture_name!r} not found in {ytd_xml_path}")


def pack_dds(source_png: Path, dest_dds: Path, fmt_info: dict) -> None:
    from PIL import Image

    pixel_format = _FORMAT_TO_PIXEL_FORMAT.get(fmt_info["format"])
    if not pixel_format:
        raise RuntimeError(f"Unsupported/unmapped DDS format {fmt_info['format']!r}")

    img = Image.open(source_png).convert("RGBA")
    img = img.resize((fmt_info["width"], fmt_info["height"]), Image.LANCZOS)
    dest_dds.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest_dds, pixel_format=pixel_format)


def run_codewalker_convert(codewalker_cli: str, xml_dir: Path, out_dir: Path, model_name: str) -> str:
    cmd = [codewalker_cli, "convert", "--in", str(xml_dir), "--out", str(out_dir), "--name", model_name]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return proc.stdout + proc.stderr if proc.returncode == 0 else f"[ERROR exit={proc.returncode}]\n{proc.stdout}{proc.stderr}"


def run_codewalker_inspect(codewalker_cli: str, ytd_path: Path) -> str:
    cmd = [codewalker_cli, "inspect", str(ytd_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return proc.stdout + proc.stderr if proc.returncode == 0 else f"[ERROR exit={proc.returncode}]\n{proc.stdout}{proc.stderr}"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--source-png", required=True)
    p.add_argument("--ytd-xml-dir", required=True)
    p.add_argument("--texture-name", required=True)
    p.add_argument("--codewalker-cli", required=True)
    p.add_argument("--model-name", required=True)
    p.add_argument("--out-dir", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    xml_dir = Path(args.ytd_xml_dir)
    ytd_xml_path = xml_dir / f"{args.model_name}.ytd.xml"
    fmt_info = read_texture_format(ytd_xml_path, args.texture_name)
    print(f"[INFO] Original texture format: {fmt_info}")

    dest_dds = xml_dir / fmt_info["filename"]
    pack_dds(Path(args.source_png), dest_dds, fmt_info)
    print(f"[OK] Packed {dest_dds} as {fmt_info['format']} {fmt_info['width']}x{fmt_info['height']}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    convert_log = run_codewalker_convert(args.codewalker_cli, xml_dir, out_dir, args.model_name)
    print(f"[CONVERT]\n{convert_log}")

    ytd_path = out_dir / f"{args.model_name}.ytd"
    if ytd_path.exists():
        inspect_log = run_codewalker_inspect(args.codewalker_cli, ytd_path)
        print(f"[INSPECT]\n{inspect_log}")
    else:
        print(f"[WARN] {ytd_path} was not produced by convert, skipping inspect.")


if __name__ == "__main__":
    main()
