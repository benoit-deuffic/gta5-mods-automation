"""head_swap: race coverage, bone palette and neck checks (CodeWalker-XML stand-ins)."""
import sys
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import head_swap  # noqa: E402
from head_swap import HeadSwapError, check_compatible, swap_head  # noqa: E402


def _ydd(points, palettes=("128",)):
    """Minimal .ydd.xml: one High-LOD geometry per palette, vertices as <Vertices> lines."""
    verts = "\n".join(f"{x} {y} {z}" for x, y, z in points)
    models = "".join(f'<Item><Unknown1 value="{p}"/><Geometries><Item><VertexBuffer><Layout type="GTAV1">'
                     f'<Position/></Layout><Data>{verts}</Data></VertexBuffer></Item></Geometries></Item>'
                     for p in palettes)
    return ET.fromstring(f"<DrawableDictionary><Item><DrawableModelsHigh>{models}</DrawableModelsHigh>"
                         f"</Item></DrawableDictionary>")


NECK = [(0.05, 0.0, 0.5), (0.0, 0.05, 0.5), (-0.05, 0.0, 0.5)]
UPPR = _ydd(NECK + [(0.1, 0.0, 0.3)])


def test_same_neck_and_palette_is_accepted_and_rigid_parts_are_ignored():
    check_compatible(_ydd(NECK + [(0, 0, 0.7)]), _ydd(NECK + [(0, 0.01, 0.7)], ("128", "1")), UPPR)


def test_other_skeleton_or_open_neck_is_refused():
    with pytest.raises(HeadSwapError, match="palette"):
        check_compatible(_ydd(NECK), _ydd(NECK, ("107",)), UPPR)
    with pytest.raises(HeadSwapError, match="neck"):
        check_compatible(_ydd(NECK), _ydd(NECK[:2] + [(-0.05, 0.0, 0.51)]), UPPR)


def test_a_race_missing_from_the_donor_is_refused_before_anything_is_copied(tmp_path):
    ped, donor, stage = tmp_path / "ped", tmp_path / "donor", tmp_path / "stage"
    for d in (ped, donor, stage):
        d.mkdir()
    for name in ("head_diff_000_a_ara.ytd", "head_diff_000_f_whi.ytd"):
        (ped / name).write_bytes(b"x")
    (donor / "head_004_r.ydd").write_bytes(b"x")
    (donor / "head_diff_004_a_pak.ytd").write_bytes(b"x")
    with pytest.raises(HeadSwapError, match=r"\['ara', 'whi'\]"):
        swap_head(ped, stage, donor / "head_004_r.ydd")
    assert not list(stage.iterdir())


def test_textures_are_copied_by_race_under_the_ped_letters(tmp_path, monkeypatch):
    ped, donor, stage = tmp_path / "ped", tmp_path / "donor", tmp_path / "stage"
    for d in (ped, donor, stage):
        d.mkdir()
    for name in ("head_000_r.ydd", "uppr_000_r.ydd", "head_diff_000_a_ara.ytd", "head_diff_000_f_whi.ytd"):
        (ped / name).write_bytes(b"old")
    (donor / "head_002_r.ydd").write_bytes(b"new head")
    (donor / "head_diff_002_a_whi.ytd").write_bytes(b"new whi")
    (donor / "head_diff_002_e_ara.ytd").write_bytes(b"new ara")

    def fake_export(binary, out):
        out.mkdir(parents=True)
        root = UPPR if binary.name.startswith("uppr") else _ydd(NECK)
        ET.ElementTree(root).write(out / f"{binary.name}.xml")
        return SimpleNamespace(status="converted", message="")

    monkeypatch.setattr(head_swap, "export_binary_to_xml", fake_export)
    report = swap_head(ped, stage, donor / "head_002_r.ydd")
    assert (stage / "head_000_r.ydd").read_bytes() == b"new head"
    assert (stage / "head_diff_000_a_ara.ytd").read_bytes() == b"new ara"  # ped's letter, donor's race
    assert (stage / "head_diff_000_f_whi.ytd").read_bytes() == b"new whi"
    assert report["races"] == {"ara": "head_diff_002_e_ara.ytd", "whi": "head_diff_002_a_whi.ytd"}
