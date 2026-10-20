"""component_swap: a component's drawables replaced, files renamed, .ymt entry rewritten."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from component_swap import ComponentSwapError, Pick, barefoot_feet, set_component  # noqa: E402


def _ymt(hair, feet):
    """hair/feet: per drawable, its texIds. availComp: hair -> 0, feet -> 1, the rest absent."""
    def comp(drawables):
        items = "".join("<Item><propMask value=\"17\"/><aTexData>" + "".join(
            f"<Item><texId value=\"{t}\"/><distribution value=\"255\"/></Item>" for t in tex)
            + "</aTexData></Item>" for tex in drawables)
        return f"<Item><numAvailTex value=\"{sum(map(len, drawables))}\"/><aDrawblData3>{items}</aDrawblData3></Item>"
    infos = "".join(f"<Item><flags value=\"{c}{d}\"/><pedXml_compIdx value=\"{c}\"/><pedXml_drawblIdx value=\"{d}\"/></Item>"
                    for c, n in ((2, len(hair)), (6, len(feet))) for d in range(n))
    return ET.fromstring(f"<CPedVariationInfo><availComp>255 255 0 255 255 255 1 255 255 255 255 255</availComp>"
                         f"<aComponentData3>{comp(hair)}{comp(feet)}</aComponentData3>"
                         f"<compInfos>{infos}</compInfos></CPedVariationInfo>")


def _files(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    for n in names:
        (folder / n).write_text(n)


def test_one_donor_drawable_with_one_colour_replaces_the_component(tmp_path):
    stage, donor = tmp_path / "stage", tmp_path / "Donor"
    _files(stage, ["hair_000_u.ydd", "hair_001_u.ydd", "hair_diff_000_a_uni.ytd", "hair_diff_001_a_uni.ytd",
                   "feet_000_r.ydd"])
    _files(donor, ["hair_004_u.ydd"] + [f"hair_diff_004_{x}_uni.ytd" for x in "abcde"])
    ymt, donor_ymt = _ymt([[0], [0]], [[1]]), _ymt([[0]] * 4 + [[0] * 5], [[1]])

    set_component(stage, ymt, "hair", [Pick(donor, donor_ymt, 4, "e")])

    assert sorted(p.name for p in stage.glob("hair*")) == ["hair_000_u.ydd", "hair_diff_000_a_uni.ytd"]
    assert (stage / "hair_000_u.ydd").read_text() == "hair_004_u.ydd"
    assert (stage / "hair_diff_000_a_uni.ytd").read_text() == "hair_diff_004_e_uni.ytd"  # colour e -> letter a
    hair = ymt.find("aComponentData3")[0]
    assert hair.find("numAvailTex").get("value") == "1" and len(hair.find("aDrawblData3")) == 1
    infos = [(i.find("pedXml_compIdx").get("value"), i.find("pedXml_drawblIdx").get("value"), i.find("flags").get("value"))
             for i in ymt.find("compInfos")]
    assert infos == [("2", "0", "24"), ("6", "0", "60")]  # donor's hair 4 item, renumbered; feet untouched
    assert (stage / "feet_000_r.ydd").exists()


def test_keeping_only_the_barefoot_drawable_keeps_its_races(tmp_path):
    own, stage = tmp_path / "Ped", tmp_path / "stage"
    names = ["feet_000_r.ydd", "feet_001_u.ydd", "feet_002_r.ydd", "feet_diff_000_a_whi.ytd",
             "feet_diff_001_a_uni.ytd", "feet_diff_002_a_ara.ytd", "feet_diff_002_b_whi.ytd"]
    _files(own, names)
    _files(stage, names)
    ymt = _ymt([[0]], [[1], [0], [5, 1]])

    set_component(stage, ymt, "feet", [Pick(own, _ymt([[0]], [[1], [0], [5, 1]]), 2)])

    assert sorted(p.name for p in stage.glob("feet*")) == ["feet_000_r.ydd", "feet_diff_000_a_ara.ytd",
                                                          "feet_diff_000_b_whi.ytd"]
    feet = ymt.find("aComponentData3")[1]
    assert [t.find("texId").get("value") for t in feet.find("aDrawblData3")[0].find("aTexData")] == ["5", "1"]
    assert feet.find("numAvailTex").get("value") == "2"


def test_missing_texture_or_component_is_refused_before_touching_files(tmp_path):
    stage, donor = tmp_path / "stage", tmp_path / "Donor"
    _files(stage, ["hair_000_u.ydd", "hair_diff_000_a_uni.ytd"])
    _files(donor, ["hair_004_u.ydd", "hair_diff_004_a_uni.ytd"])
    with pytest.raises(ComponentSwapError, match="no texture e"):
        set_component(stage, _ymt([[0]], [[1]]), "hair", [Pick(donor, _ymt([[0]] * 5, [[1]]), 4, "e")])
    with pytest.raises(ComponentSwapError, match="no jbib"):
        set_component(stage, _ymt([[0]], [[1]]), "jbib", [Pick(donor, _ymt([[0]] * 5, [[1]]), 4)])
    assert (stage / "hair_000_u.ydd").exists()


def test_barefoot_feet_is_the_one_with_a_skin_texture(tmp_path):
    folder, xml = tmp_path / "Ped", tmp_path / "Ped_xml"
    _files(folder, ["feet_000_r.ydd", "feet_002_r.ydd"])
    xml.mkdir()
    for n, dds in (("000_a_whi", "feet_diff_001_a_uni.dds"), ("002_f_whi", "mp_fm_skin_f_fe_whi.dds")):
        (xml / f"feet_diff_{n}.ytd.xml").write_text(f"<TextureDictionary><Item><FileName>{dds}</FileName></Item></TextureDictionary>")
    assert barefoot_feet(folder, xml) == "feet_002_r.ydd"


def test_a_style_without_letters_keeps_every_colour(tmp_path):
    stage, donor = tmp_path / "stage", tmp_path / "Donor"
    _files(stage, ["hair_000_u.ydd", "hair_diff_000_a_uni.ytd"])
    _files(donor, ["hair_004_u.ydd"] + [f"hair_diff_004_{x}_uni.ytd" for x in "abcde"])
    ymt = _ymt([[0]], [[1]])
    set_component(stage, ymt, "hair", [Pick(donor, _ymt([[0]] * 4 + [[0] * 5], [[1]]), 4)])
    assert sorted(p.name for p in stage.glob("hair_diff*")) == [f"hair_diff_000_{x}_uni.ytd" for x in "abcde"]
    assert ymt.find("aComponentData3")[0].find("numAvailTex").get("value") == "5"
