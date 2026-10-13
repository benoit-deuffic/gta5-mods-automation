import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import xml_manifest_updater as xmu


def test_add_ped_creates_manifest_if_missing(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    assert not manifest.exists()

    changed = xmu.add_ped_to_manifest(manifest, "BeachSurfer01", gender="female")

    assert changed
    assert manifest.exists()
    assert xmu.list_registered_peds(manifest) == ["BeachSurfer01"]


def test_add_ped_is_idempotent_by_name(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "BeachSurfer01", gender="female")
    xmu.add_ped_to_manifest(manifest, "BeachSurfer01", gender="female")

    names = xmu.list_registered_peds(manifest)
    assert names == ["BeachSurfer01"]


def test_multiple_peds_do_not_overwrite_each_other(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "BeachSurfer01", gender="female")
    xmu.add_ped_to_manifest(manifest, "SurferDude02", gender="male")
    xmu.add_ped_to_manifest(manifest, "BizWoman03", gender="female")

    names = xmu.list_registered_peds(manifest)
    assert set(names) == {"BeachSurfer01", "SurferDude02", "BizWoman03"}


def test_gender_specific_fields_are_consistent(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "SurferDude02", gender="male")

    tree = ET.parse(manifest)
    item = tree.getroot().find("InitDatas").find("Item")
    assert item.findtext("PoseMatcherName") == "Male"
    assert item.findtext("PoseMatcherProneName") == "Male_prone"
    assert item.findtext("PedCapsuleName") == "STANDARD_MALE"
    assert item.findtext("ExpressionSetName") == "expr_set_ambient_male"


def test_invalid_gender_raises(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    with pytest.raises(xmu.ManifestError):
        xmu.add_ped_to_manifest(manifest, "Bad01", gender="other")


def test_manifest_is_well_formed_xml(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "BeachSurfer01", gender="female")
    # Will raise ParseError if malformed.
    ET.parse(manifest)


def test_streamed_gfx_flag_reflected(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "StreamedOne", gender="female", is_streamed_gfx=True)
    tree = ET.parse(manifest)
    item = tree.getroot().find("InitDatas").find("Item")
    flag = item.find("IsStreamedGfx")
    assert flag.attrib["value"] == "true"


def test_female_peds_use_the_male_pose_matcher_the_game_knows(tmp_path: Path):
    # "Female"/"Female_prone" crash the game at launch (tested in game)
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "Alexandra", gender="female", is_streamed_gfx=True)
    item = ET.parse(manifest).getroot().find("InitDatas").find("Item")
    assert item.findtext("PoseMatcherName") == "Male"
    assert item.findtext("PoseMatcherProneName") == "Male_prone"
    assert item.findtext("PedCapsuleName") == "STANDARD_FEMALE"


def test_existing_entries_are_kept_identical_and_in_order(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    for name in ("A", "B", "C"):
        xmu.add_ped_to_manifest(manifest, name, gender="female")

    def items():  # content, not the indentation around it
        return [[(f.tag, f.text, f.attrib) for f in i] for i in ET.parse(manifest).getroot().find("InitDatas")]

    before = items()
    xmu.add_ped_to_manifest(manifest, "D", gender="male")
    assert items()[:3] == before
    assert xmu.list_registered_peds(manifest) == ["A", "B", "C", "D"]


def test_register_ped_backs_up_then_appends(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "Camille", gender="female", is_streamed_gfx=True)
    original = manifest.read_bytes()

    assert xmu.register_ped(manifest, "Eva") == "registered"
    backups = list(tmp_path.glob("ap_m.xml.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert xmu.list_registered_peds(manifest) == ["Camille", "Eva"]
    item = ET.parse(manifest).getroot().find("InitDatas")[1]
    assert item.find("IsStreamedGfx").get("value") == "true"


def test_register_ped_never_rewrites_an_existing_entry(tmp_path: Path):
    manifest = tmp_path / "ap_m.xml"
    xmu.add_ped_to_manifest(manifest, "Eva", gender="female", is_streamed_gfx=True)
    manifest.write_text(manifest.read_text().replace("BAR_PERSON_PVG", "HAND_EDITED"))  # user's own edit
    before = manifest.read_bytes()

    assert xmu.register_ped(manifest, "Eva") == "already registered"
    assert manifest.read_bytes() == before and not list(tmp_path.glob("*.bak-*"))
