import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import ymt_builder


def test_ymt_xml_well_formed(tmp_path: Path):
    out = tmp_path / "Test01.ymt.xml"
    ymt_builder.write_ymt_xml(out, "Test01")
    tree = ET.parse(out)
    assert tree.getroot().tag == "CPedVariationInfo"
    assert tree.getroot().findtext("Name") == "Test01"


def test_ymt_default_components_present():
    tree = ymt_builder.build_ymt_xml("Test01")
    root = tree.getroot()
    avail_comp = root.find("availComp").text.split()
    # order: head berd hair uppr lowr hand feet teef accs task decl jbib
    assert avail_comp[0] == "0"   # head present
    assert avail_comp[1] == "255"  # berd absent
    assert avail_comp[3] == "0"   # uppr present
    assert avail_comp[4] == "0"   # lowr present


def test_ymt_comp_infos_count_matches_present_components():
    tree = ymt_builder.build_ymt_xml("Test01", present_components=("head", "uppr"))
    root = tree.getroot()
    comp_infos = root.find("compInfos").findall("Item")
    assert len(comp_infos) == 2
