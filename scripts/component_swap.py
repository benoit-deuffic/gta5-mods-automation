"""Replace the drawables of one component of a streamed ped (its own or another
ped's) and rewrite that component's entry in the .ymt (CPedVariationInfo).

    set_component(stage_dir, ymt_root, "feet", [Pick(own_folder, own_ymt, 2)])          # barefoot only
    set_component(stage_dir, ymt_root, "hair", [Pick(bg_folder, bg_ymt, 4, "e")])        # one hair, one colour

A streamed ped finds a component's files by NAME: drawable j is
`<comp>_<jjj>_<u|r>.ydd`, its i-th texture `<comp>_diff_<jjj>_<letter i>_<race>.ytd`
(letter a = 1st aTexData item, b = 2nd...). The .ymt lists, per drawable, one
aTexData item per texture (texId = race code, 0 = universal) plus a compInfos
item; numAvailTex is the component's texture total. So a pick is copied as
the source's drawable item (deep copy, only the kept textures), its files
renamed to the new drawable index / letters, and its compInfos item moved
over with the new drawblIdx. The source ped must use the same skeleton
(identical .yft): its drawables index those bones.
"""
from __future__ import annotations

import copy
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

COMPONENTS = ("head", "berd", "hair", "uppr", "lowr", "hand", "feet", "teef", "accs", "task", "decl", "jbib")


class ComponentSwapError(Exception):
    pass


@dataclass(frozen=True)
class Pick:
    folder: Path          # streamed ped folder holding the drawable's binaries (<ped>/)
    ymt: ET.Element       # that ped's .ymt as CodeWalker XML
    drawable: int
    letters: str | None = None  # texture letters to keep, in this order (None = all)


def _entry(ymt: ET.Element, component: str) -> ET.Element:
    slot = COMPONENTS.index(component)
    index = int(ymt.find("availComp").text.split()[slot])
    if index == 255:
        raise ComponentSwapError(f"the ped has no {component} component")
    return ymt.find("aComponentData3")[index]


def _one(folder: Path, pattern: str) -> Path:
    found = sorted(folder.glob(pattern))
    if len(found) != 1:
        raise ComponentSwapError(f"expected one {pattern} in {folder}, found {[p.name for p in found]}")
    return found[0]


def same_skeleton(ped_yft: Path, donor_folder: Path) -> bool:
    """The donor ped's .yft (next to its folder) is byte-identical to the ped's."""
    donor_yft = donor_folder.parent / f"{donor_folder.name}.yft"
    return donor_yft.is_file() and donor_yft.read_bytes() == ped_yft.read_bytes()


def set_component(stage: Path, ymt: ET.Element, component: str, picks: list[Pick]) -> list[dict]:
    """Make `picks` the only drawables of `component` in `stage` (files) and
    `ymt` (edited in place). Returns one {"source", "textures"} per new drawable."""
    if not picks:
        raise ComponentSwapError(f"no drawable picked for {component}")
    slot = str(COMPONENTS.index(component))
    entry = _entry(ymt, component)
    plan = []  # (drawable item, ydd, [(ytd, letter, race)], compInfo item)
    for pick in picks:
        source = _entry(pick.ymt, component).find("aDrawblData3")
        if not 0 <= pick.drawable < len(source):
            raise ComponentSwapError(f"{pick.folder.name} has no {component} drawable {pick.drawable}")
        item = copy.deepcopy(source[pick.drawable])
        textures = list(item.find("aTexData"))
        letters = pick.letters or "abcdefghijklmnopqrstuvwxyz"[:len(textures)]
        kept = []
        for letter in letters:
            i = ord(letter) - ord("a")
            if not 0 <= i < len(textures):
                raise ComponentSwapError(f"{component} {pick.drawable} of {pick.folder.name} has no texture {letter}")
            kept.append((textures[i], _one(pick.folder, f"{component}_diff_{pick.drawable:03d}_{letter}_*.ytd")))
        tex_data = item.find("aTexData")
        for t in list(tex_data):
            tex_data.remove(t)
        for t, _ytd in kept:
            tex_data.append(t)
        info = next((i for i in pick.ymt.find("compInfos")
                     if i.find("pedXml_compIdx").get("value") == slot
                     and i.find("pedXml_drawblIdx").get("value") == str(pick.drawable)), None)
        if info is None:
            raise ComponentSwapError(f"no compInfos item for {component} {pick.drawable} in {pick.folder.name}")
        plan.append((item, _one(pick.folder, f"{component}_{pick.drawable:03d}_*.ydd"),
                     [ytd for _t, ytd in kept], copy.deepcopy(info)))

    for old in list(stage.glob(f"{component}_[0-9][0-9][0-9]_*.ydd")) + list(
            stage.glob(f"{component}_diff_[0-9][0-9][0-9]_*.ytd")):
        old.unlink()
    drawables = entry.find("aDrawblData3")
    for old in list(drawables):
        drawables.remove(old)
    infos = ymt.find("compInfos")
    positions = [k for k, i in enumerate(infos) if i.find("pedXml_compIdx").get("value") == slot]
    at = positions[0] if positions else len(infos)
    for k in reversed(positions):
        infos.remove(infos[k])

    report = []
    for j, (item, ydd, ytds, info) in enumerate(plan):
        suffix = ydd.name.rsplit("_", 1)[1]  # "u.ydd" / "r.ydd"
        shutil.copy2(ydd, stage / f"{component}_{j:03d}_{suffix}")
        names = []
        for k, ytd in enumerate(ytds):
            race = ytd.name.rsplit("_", 1)[1]  # "uni.ytd", "whi.ytd"...
            name = f"{component}_diff_{j:03d}_{chr(ord('a') + k)}_{race}"
            shutil.copy2(ytd, stage / name)
            names.append(name)
        drawables.append(item)
        info.find("pedXml_drawblIdx").set("value", str(j))
        infos.insert(at + j, info)
        report.append({"source": str(ydd), "textures": [f"{y.name} -> {n}" for y, n in zip(ytds, names)]})
    entry.find("numAvailTex").set("value", str(sum(len(p[2]) for p in plan)))
    return report


def barefoot_feet(folder: Path, xml_folder: Path) -> str | None:
    """File name of the ped's barefoot feet drawable: the feet drawable whose
    diffuse is a SKIN texture (CatalinaNude feet_002_r -> mp_fm_skin_f_fe_*.dds;
    the others are shoes), or None. `xml_folder` = its CodeWalker-XML export."""
    for ytd in sorted(xml_folder.glob("feet_diff_[0-9][0-9][0-9]_*.ytd.xml")):
        dds = (ET.parse(ytd).getroot().findtext(".//FileName") or "").lower()
        if "_skin_" in dds:
            index = ytd.name.split("_")[2]
            found = sorted(folder.glob(f"feet_{index}_*.ydd"))
            if found:
                return found[0].name
    return None
