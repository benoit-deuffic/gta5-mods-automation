"""Swap a streamed ped's face for another ped's head, keeping the .ymt valid.

A streamed ped's `.ymt` gives each head drawable a list of textures, and
each texture a RACE (`texId`: 1 whi, 2 bla, 3 chi, 4 lat, 5 ara, 9 ita,
10 pak -- the same code as the filename suffix). The game picks one race per
ped and takes the matching texture on every `_r` component (head, uppr,
lowr, feet...), so the face and the body skin tones stay paired.

`swap_head` replaces head drawable 000 of a packaged ped with a donor head
(`<donor>/head_NNN_r.ydd`) and, for every texture the ped's `.ymt` declares
for it (`head_diff_000_<letter>_<race>.ytd`), the donor's texture of the SAME
race under the ped's own file name. The `.ymt` is therefore unchanged: same
letters, same races, same counts. Refused unless the donor fits:
- it has a texture for every race the ped's head 000 declares (otherwise a
  ped drawn with that race would get no face texture);
- same bone palette size for its skinned models (`Unknown1`: indices into
  the same skeleton);
- same neck opening: the donor's neck vertices weld to the ped's `uppr_000`
  exactly where the original head's do (no gap at the neck).
Other head drawables (head_001...) stay the ped's own.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from body_morphology import shared_vertices
from codewalker_convert import export_binary_to_xml
from layered_ped_builder import high_lod_positions

HEAD_DIFF_RE = re.compile(r"^head_diff_(\d{3})_([a-z])_([a-z]{3})\.ytd$")


class HeadSwapError(Exception):
    pass


def head_textures(folder: Path, index: int) -> dict[str, Path]:
    """{race: .ytd} of head drawable `index` in a streamed ped folder."""
    out = {}
    for p in folder.iterdir():
        m = HEAD_DIFF_RE.match(p.name)
        if m and int(m.group(1)) == index:
            out.setdefault(m.group(3), p)
    return out


def _palette(root: ET.Element) -> set[str]:
    """Bone-palette sizes of the SKINNED models (`Unknown1` > 1). A model
    bound to a single bone (1, e.g. a rigid part on Beachgirl5's head_001)
    indexes the skeleton directly: fine whenever the skeleton is the same."""
    return {str(e.get("value")) for e in root.iter("Unknown1") if e.get("value") != "1"}


def _neck(head: list, uppr: list) -> set:
    return {uppr[i] for i, _j in shared_vertices(uppr, head)}


def check_compatible(original_head: ET.Element, donor_head: ET.Element, uppr: ET.Element) -> None:
    """Raise HeadSwapError unless the donor head drives the same bones and
    closes the same neck opening of `uppr` as the original head."""
    if _palette(donor_head) != _palette(original_head):
        raise HeadSwapError(f"bone palette {sorted(_palette(donor_head))} != the ped's "
                            f"{sorted(_palette(original_head))}: built for another skeleton")
    uppr_points = high_lod_positions(uppr)
    expected = _neck(high_lod_positions(original_head), uppr_points)
    got = _neck(high_lod_positions(donor_head), uppr_points)
    if not expected or got != expected:
        raise HeadSwapError(f"neck doesn't match: welds {len(got & expected)}/{len(expected)} of the "
                            f"body's neck vertices ({len(got - expected)} elsewhere)")


def swap_head(source_dir: Path, stage_dir: Path, donor_ydd: Path) -> dict:
    """Replace head 000 in `stage_dir` (a packaged copy of `source_dir`, the
    ped's original <ped>/ folder) by `donor_ydd` + its race textures.
    Returns {"donor", "races": {race: donor texture}}."""
    m = re.match(r"^head_(\d{3})_r\.ydd$", donor_ydd.name)
    if not m or not donor_ydd.is_file():
        raise HeadSwapError(f"not a head drawable: {donor_ydd}")
    wanted = head_textures(source_dir, 0)
    offered = head_textures(donor_ydd.parent, int(m.group(1)))
    missing = sorted(set(wanted) - set(offered))
    if not wanted or missing:
        raise HeadSwapError(f"{donor_ydd.name} has no texture for race(s) {missing} "
                            f"that the ped's head declares ({sorted(wanted)})")
    with tempfile.TemporaryDirectory() as tmp:
        roots = {}
        for key, binary in (("original", source_dir / "head_000_r.ydd"), ("donor", donor_ydd),
                            ("uppr", source_dir / "uppr_000_r.ydd")):
            out = Path(tmp) / key
            result = export_binary_to_xml(binary, out)
            xml = out / f"{binary.name}.xml"
            if result.status != "converted" or not xml.is_file():
                raise HeadSwapError(f"could not read {binary} ({result.status}): {result.message}")
            roots[key] = ET.parse(xml).getroot()
    check_compatible(roots["original"], roots["donor"], roots["uppr"])
    shutil.copy2(donor_ydd, stage_dir / "head_000_r.ydd")
    for race, target in wanted.items():
        shutil.copy2(offered[race], stage_dir / target.name)
    return {"donor": str(donor_ydd), "races": {race: offered[race].name for race in sorted(wanted)}}
