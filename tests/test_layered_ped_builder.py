"""Pure unit tests for the 3-tier layered ped-construction resolver
(scripts/layered_ped_builder.py). No bpy involved -- the resolution logic
is file-system/naming only, proven against synthetic tmp_path trees that
mimic the real on-disk convention (see docs/architecture-decisions.md and
the module docstring for the full rule).
"""
import logging

from scripts.layered_ped_builder import resolve_layered_components


PED_NAME = "testped_dlc_01"


def _make_tier_file(ped_root, *parts, name):
    """Create an empty file at ped_root/<parts.../>name, making parent dirs."""
    folder = ped_root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"")
    return path


def _top_dir(ped_root):
    return (ped_root, PED_NAME)


def _mid_dir(ped_root):
    return (ped_root, PED_NAME, "base", PED_NAME)


def _base_all_dir(ped_root):
    return (ped_root, PED_NAME, "base", "base_all", PED_NAME)


def test_top_beats_mid_beats_base_all_for_same_component(tmp_path):
    """The core invariant, doubly emphasized by the user: when all 3 tiers
    define the SAME component code, the top tier's file must win."""
    top_file = _make_tier_file(tmp_path, *_top_dir(tmp_path), name="jbib_002_u.ydd")
    _make_tier_file(tmp_path, *_mid_dir(tmp_path), name="jbib_005_u.ydd")
    _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="jbib_000_u.ydd")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved["jbib"] == top_file


def test_component_present_in_only_one_tier_resolves(tmp_path):
    only_file = _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="feet_000_u.ydd")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved == {"feet": only_file}


def test_lowest_variant_index_wins_within_a_tier(tmp_path):
    _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="jbib_005_u.ydd")
    lowest = _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="jbib_000_u.ydd")
    _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="jbib_013_u.ydd")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved["jbib"] == lowest


def test_top_tier_does_not_recurse_into_its_own_base_subdir(tmp_path):
    """Regression test for the bug that bit a real throwaway script: the top
    tier's folder physically contains a 'base/' subdirectory (where mid and
    base_all live). A naive recursive walk would treat mid/base_all files as
    if they were top-tier files, which is wrong -- and conversely, files that
    really are mid/base_all must still resolve correctly, not get dropped."""
    top_file = _make_tier_file(tmp_path, *_top_dir(tmp_path), name="lowr_003_r.ydd")
    mid_file = _make_tier_file(tmp_path, *_mid_dir(tmp_path), name="decl_027_u.ydd")
    base_all_file = _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="accs_000_u.ydd")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    # All three resolve to their real owning tier...
    assert resolved["lowr"] == top_file
    assert resolved["decl"] == mid_file
    assert resolved["accs"] == base_all_file
    # ...and the top tier's OWN resolution must not have picked up decl/accs
    # as though they lived directly under <ped_name>/ (they're nested under
    # <ped_name>/base/...). Only 3 components total should resolve.
    assert len(resolved) == 3


def test_non_conforming_filenames_are_skipped_with_warning_not_crash(tmp_path):
    _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="readme_notes.txt")
    _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="randomfile.ydd")
    good_file = _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="hand_000_u.ydd")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved == {"hand": good_file}


def test_non_conforming_filename_logs_a_warning(tmp_path, caplog):
    _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="not_a_component.ydd")

    with caplog.at_level(logging.WARNING):
        resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved == {}
    assert any("non-conforming" in record.message for record in caplog.records)


def test_resolution_works_against_codewalker_xml_extension(tmp_path):
    """The resolution logic must be extension-agnostic: it runs the same way
    against raw .ydd and already-exported .ydd.xml (CodeWalker-XML) files --
    the real pipeline needs the .xml form right before Sollumz import."""
    xml_file = _make_tier_file(tmp_path, *_top_dir(tmp_path), name="uppr_000_r.ydd.xml")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved == {"uppr": xml_file}


def test_mixed_raw_ydd_and_xml_tiers_both_resolve_independently(tmp_path):
    """A tier can mix raw .ydd next to already-converted .ydd.xml without
    the resolver confusing the two as separate 'different stems'."""
    xml_file = _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="uppr_000_r.ydd.xml")
    raw_file = _make_tier_file(tmp_path, *_base_all_dir(tmp_path), name="feet_000_u.ydd")

    resolved = resolve_layered_components(tmp_path, PED_NAME)

    assert resolved["uppr"] == xml_file
    assert resolved["feet"] == raw_file
