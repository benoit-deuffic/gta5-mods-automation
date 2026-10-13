"""Pure tests for reshaping every body variant (uppr_000..005 / lowr_000..002):
finding the variants, pairing each with its seam partner, the welded-seam
detection and the replayed glute check. No bpy."""
import xml.etree.ElementTree as ET

from scripts.body_morphology import GLUTE_REPLAY_TOLERANCE, glute_targets_missed, shared_vertices
from scripts.layered_ped_builder import component_variants, high_lod_positions, skin_variant_combos


def test_component_variants_lists_one_slot_by_variant_index(tmp_path):
    for name in ("uppr_002_r.ydd.xml", "uppr_000_r.ydd.xml", "uppr_010_r.ydd.xml", "lowr_000_r.ydd.xml",
                 "uppr_001_r.ydd", "uppr_diff_000_a_whi.ytd.xml"):
        (tmp_path / name).write_text("")
    assert [p.name for p in component_variants(tmp_path / "uppr_000_r.ydd.xml")] == \
        ["uppr_000_r.ydd.xml", "uppr_002_r.ydd.xml", "uppr_010_r.ydd.xml"]
    # the binaries' folder works the same way (ped_package)
    assert [p.name for p in component_variants(tmp_path / "uppr_001_r.ydd")] == ["uppr_001_r.ydd"]


def test_each_variant_is_built_once_with_its_seam_partner():
    # CatalinaNude: uppr_002 and lowr_002 are their own body, welded only to
    # each other; every other uppr shares its waist with lowr_000/001
    variants = {"uppr": ["u0", "u1", "u2", "u3"], "lowr": ["l0", "l1", "l2"]}
    pairs = {("u2", "l2")} | {(u, lo) for u in ("u0", "u1", "u3") for lo in ("l0", "l1")}
    combos = skin_variant_combos(variants, lambda u, lo: (u, lo) in pairs)
    assert combos == [{"uppr": "u0", "lowr": "l0"},  # the main build first
                      {"uppr": "u1", "lowr": "l0"}, {"uppr": "u2", "lowr": "l2"}, {"uppr": "u3", "lowr": "l0"},
                      {"uppr": "u0", "lowr": "l1"}]  # l2's pair already there


def test_a_variant_welded_to_nothing_is_built_with_the_lowest_partner():
    combos = skin_variant_combos({"uppr": ["u0", "u1"], "lowr": ["l0"]}, lambda u, lo: False)
    assert combos == [{"uppr": "u0", "lowr": "l0"}, {"uppr": "u1", "lowr": "l0"}]


def test_shared_vertices_finds_the_welded_seam_only():
    uppr = [(0.0, 0.0, 0.08), (0.1, 0.0, 0.08), (0.0, 0.0, 0.3)]
    lowr = [(0.0, 0.0, 0.0), (0.1002, 0.0, 0.08), (0.0, 0.0003, 0.08)]
    assert shared_vertices(uppr, lowr) == [(0, 2), (1, 1)]
    assert shared_vertices(uppr, [(0.0, 0.0, 0.0812)]) == []  # 1.2 mm apart: not welded


def test_high_lod_positions_reads_only_the_high_lod():
    root = ET.fromstring("""<DrawableDictionary><Item>
      <DrawableModelsHigh><Item><Geometries><Item><VertexBuffer>
        <Layout><BlendWeights /><Position /><Normal /></Layout>
        <Data2>
          255 0 0 0   0.1 0.2 0.3   0 0 1
          255 0 0 0   -1 -2 -3   0 0 1
        </Data2></VertexBuffer></Item></Geometries></Item></DrawableModelsHigh>
      <DrawableModelsMedium><Item><Geometries><Item><VertexBuffer>
        <Layout><Position /></Layout><Data2>9 9 9</Data2></VertexBuffer></Item></Geometries></Item>
      </DrawableModelsMedium></Item></DrawableDictionary>""")
    assert high_lod_positions(root) == [(0.1, 0.2, 0.3), (-1.0, -2.0, -3.0)]


def test_replayed_glutes_are_checked_against_the_targets():
    targets = {"lateral_whr": 0.78, "posterior_whr": 0.72}
    assert glute_targets_missed({"lateral_whr": 0.787, "posterior_whr": 0.711}, targets) == {}
    off = 0.78 + GLUTE_REPLAY_TOLERANCE + 0.002
    assert glute_targets_missed({"lateral_whr": off, "posterior_whr": 0.72}, targets) == \
        {"lateral_whr": (0.78, off)}
    assert glute_targets_missed(None, targets) == {"lateral_whr": (0.78, None), "posterior_whr": (0.72, None)}
    assert glute_targets_missed(None, {}) == {}
    # the apex position keeps its own tolerance
    assert glute_targets_missed({"apex_position": 0.53}, {"apex_position": 0.5}) == {"apex_position": (0.5, 0.53)}
