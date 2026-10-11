import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import ymt_builder


def test_ymt_xml_well_formed(tmp_path: Path):
    out = tmp_path / "Test01.ymt.xml"
    ymt_builder.write_ymt_xml(out, "Test01")
    tree = ET.parse(out)
    root = tree.getroot()
    assert root.tag == "CPedVariationInfo"
    # Real struct has no "Name" field (see module docstring); the four
    # preamble booleans must be present instead (point 11 fix).
    for tag in ("bHasTexVariations", "bHasDrawblVariations", "bHasLowLODs", "bIsSuperLOD"):
        assert root.find(tag) is not None, f"missing required field {tag}"
        assert root.find(tag).attrib["value"] in ("true", "false")


def test_ymt_default_components_present():
    tree = ymt_builder.build_ymt_xml("Test01")
    root = tree.getroot()
    avail_comp = root.find("availComp").text.split()
    # order: head berd hair uppr lowr hand feet teef accs task decl jbib
    assert avail_comp[0] == "0"   # head present
    assert avail_comp[1] == "255"  # berd absent
    assert avail_comp[3] == "0"   # uppr present
    assert avail_comp[4] == "0"   # lowr present


def test_ymt_component_data3_count_matches_present_components():
    """aComponentData3 (not compInfos, which is intentionally left empty --
    see module docstring KNOWN SIMPLIFICATION) carries one CPVComponentData
    item per present component.
    """
    tree = ymt_builder.build_ymt_xml("Test01", present_components=("head", "uppr"))
    root = tree.getroot()
    items = root.find("aComponentData3").findall("Item")
    assert len(items) == 2


def test_ymt_comp_infos_and_selection_sets_are_present_but_empty():
    """Documented simplification (point 11): valid but empty arrays, not
    omitted entirely and not populated with guessed data.
    """
    tree = ymt_builder.build_ymt_xml("Test01")
    root = tree.getroot()
    assert root.find("compInfos") is not None
    assert root.find("compInfos").findall("Item") == []
    assert root.find("aSelectionSets") is not None
    assert root.find("aSelectionSets").findall("Item") == []


def test_ymt_component_data_nests_drawbl_and_texture_data_correctly():
    tree = ymt_builder.build_ymt_xml("Test01", present_components=("head",))
    root = tree.getroot()
    comp_item = root.find("aComponentData3").find("Item")
    assert comp_item.find("numAvailTex").attrib["value"] == "1"
    drawbl_item = comp_item.find("aDrawblData3").find("Item")
    assert drawbl_item.find("propMask") is not None
    assert drawbl_item.find("numAlternatives") is not None
    tex_item = drawbl_item.find("aTexData").find("Item")
    assert tex_item.find("texId").attrib["value"] == "0"
    assert tex_item.find("distribution") is not None
    cloth_data = drawbl_item.find("clothData")
    assert cloth_data.find("ownsCloth").attrib["value"] == "false"


def test_ymt_prop_info_present():
    tree = ymt_builder.build_ymt_xml("Test01")
    root = tree.getroot()
    prop_info = root.find("propInfo")
    assert prop_info is not None
    assert prop_info.find("numAvailProps").attrib["value"] == "0"
