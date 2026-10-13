"""oiv_package: the .oiv layout read from PedSelector.oiv and the installed
pedselector pack (dlc.rpf > peds.rpf > <name>.yft/.ymt/<name>/)."""
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from oiv_package import DLC_RPF, OivError, build_oiv, main  # noqa: E402


def _ped(root: Path, name: str = "Camille") -> Path:
    (root / name).mkdir(parents=True)
    (root / f"{name}.yft").write_bytes(b"yft")
    (root / f"{name}.ymt").write_bytes(b"ymt")
    (root / name / "uppr_000_r.ydd").write_bytes(b"ydd")
    (root / name / "uppr_diff_000_a_whi.ytd").write_bytes(b"ytd")
    return root


def test_every_file_is_added_into_peds_rpf(tmp_path):
    out = build_oiv(_ped(tmp_path / "dist"), "Camille", tmp_path / "out")
    with zipfile.ZipFile(out) as z:
        root = ET.fromstring(z.read("assembly.xml"))
        assert z.read("icon.png").startswith(b"\x89PNG")
        dlc = root.find("content/archive")
        assert dlc.get("path") == DLC_RPF and dlc.get("createIfNotExist") == "False"
        peds = dlc.find("archive")
        assert peds.get("path") == "peds.rpf" and peds.get("type") == "RPF7"
        adds = {a.get("source"): a.text for a in peds.findall("add")}
        assert adds == {
            "Camille.yft": "Camille.yft",
            "Camille.ymt": "Camille.ymt",
            "Camille/uppr_000_r.ydd": "Camille\\uppr_000_r.ydd",
            "Camille/uppr_diff_000_a_whi.ytd": "Camille\\uppr_diff_000_a_whi.ytd",
        }
        for source in adds:
            assert z.read(f"content/{source}") == (tmp_path / "dist" / source).read_bytes()


def test_package_id_is_stable_per_name(tmp_path):
    ids = []
    for i in range(2):
        out = build_oiv(_ped(tmp_path / f"d{i}"), "Camille", tmp_path / f"o{i}")
        with zipfile.ZipFile(out) as z:
            ids.append(ET.fromstring(z.read("assembly.xml")).get("id"))
    assert ids[0] == ids[1]


def test_incomplete_ped_is_refused(tmp_path):
    ped = _ped(tmp_path / "dist")
    (ped / "Camille.ymt").unlink()
    with pytest.raises(OivError, match="Camille.ymt"):
        build_oiv(ped, "Camille", tmp_path / "out")
    assert main(["--ped-dir", str(ped), "--name", "Camille", "--out", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out" / "Camille.oiv").exists()


def test_description_is_cdata(tmp_path):
    out = build_oiv(_ped(tmp_path / "dist"), "Camille", tmp_path / "out")
    with zipfile.ZipFile(out) as z:
        raw = z.read("assembly.xml").decode("utf-8")
    assert "<description><![CDATA[Ped Camille" in raw
    assert "PedSelector" in ET.fromstring(raw).find("metadata/description").text


def test_icon_must_be_128px(tmp_path):
    from oiv_package import _solid_png
    icon = tmp_path / "icon.png"
    icon.write_bytes(_solid_png(64))
    with pytest.raises(OivError, match="128x128"):
        build_oiv(_ped(tmp_path / "dist"), "Camille", tmp_path / "out", icon)
    icon.write_bytes(_solid_png(128))
    build_oiv(_ped(tmp_path / "dist2"), "Camille", tmp_path / "out", icon)


def test_non_png_icon_is_refused(tmp_path):
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"GIF89a")
    with pytest.raises(OivError, match="not a PNG"):
        build_oiv(_ped(tmp_path / "dist"), "Camille", tmp_path / "out", icon)
