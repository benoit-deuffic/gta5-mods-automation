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
