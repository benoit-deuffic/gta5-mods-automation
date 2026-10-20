"""Resolve the real 3-tier "layered patch" convention used by the user to
supply raw extracted ped assets under ``peds/<ped_name>/`` (see
docs/architecture-decisions.md for the numbered decision entry; this
docstring mirrors it so the rule lives next to the code that enforces it).

The user cannot export a whole .rpf/.dlc from OpenIV, so they hand-extract
files from OpenIV/CodeWalker into three layered folders, lowest priority
first:

  1. ``base/base_all/<ped_name>/``  -- LOWEST priority: the full catalog of
     every component variant for this ped (every numbered jbib/lowr/hand/
     feet/hair/uppr/accs/decl/berd/task variant that exists for it).
  2. ``base/<ped_name>.ymt`` + ``base/<ped_name>/``  -- MID priority: this
     specific DLC look's own base overrides.
  3. ``<ped_name>/`` (directly under ``ped_root``) + ``<ped_name>.ymt`` at
     the very top  -- HIGHEST priority, applied LAST: the actual "patch" for
     this specific final in-game look.

The user's own words (verbatim, 2026-10-12): **"le niveau le plus haut doit
etre le rendu final dans gta"** -- the top tier must be what actually
renders in-game. For any component code present in the top tier, that file
ALWAYS wins over mid and base_all, no exceptions.

Resolution rule per component code (e.g. ``jbib``, ``lowr``, ``uppr``, ...):
  - Within one tier, if multiple numbered variants exist for the same
    component (e.g. ``jbib_000_u.ydd`` and ``jbib_005_u.ydd``), the LOWEST
    variant index is that tier's representative.
  - Across tiers: top beats mid beats base_all. The last tier (in priority
    order) that defines a component code is the one whose file is selected,
    full stop -- a component only present in base_all still resolves to the
    base_all file; a component overridden at any higher tier always uses the
    highest tier that defines it, never a lower one, regardless of variant
    index at that higher tier.

PITFALL (bit a real throwaway script earlier, now permanently guarded by a
regression test): the top tier's OWN folder also contains a nested ``base/``
subdirectory (that's where mid/base_all physically live, see the directory
layout above). A naive ``os.walk()`` over the top tier's folder would also
walk into ``base/`` and wrongly treat mid/base_all files as if they were
top-tier files. Each tier must only be scanned for files DIRECTLY inside its
own folder (``Path.iterdir()``, not recursive), never nested.

This module is pure Python / pathlib, no bpy dependency -- it runs BEFORE
Sollumz import, picking which files get fed into the Blender import step
(see TODO near the bottom pointing at scripts/blender_rig_export.py).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Component code prefixes recognized by the real CPedVariationInfo convention,
# matched case-insensitively at the start of the filename, followed by an
# underscore and a numeric variant index: e.g. "jbib_000_u.ydd",
# "accs_diff_000_a_uni.ytd" (component "accs", we don't need the rest).
_COMPONENT_RE = re.compile(
    r"^(jbib|lowr|hand|accs|decl|uppr|feet|hair|berd|task|head|teef)_(\d+)",
    re.IGNORECASE,
)

# Extensions this resolver considers "the drawable file" for a component.
# Raw CodeWalker export uses .ydd; the pipeline step that runs just before
# Sollumz import works against the already-converted CodeWalker-XML form.
RECOGNIZED_EXTENSIONS = (".ydd.xml", ".ydd")

# Tier priority, LOWEST first -- later entries in this tuple win ties.
TIER_NAMES = ("base_all", "mid", "top")


@dataclass(frozen=True)
class ComponentMatch:
    component: str
    variant_index: int
    path: Path


def _matching_files(folder: Path, extensions: tuple[str, ...] = RECOGNIZED_EXTENSIONS) -> list[Path]:
    """Return files directly inside ``folder`` (NOT recursive) whose name
    ends with one of ``extensions``. Deliberately uses ``iterdir()`` rather
    than ``glob("**/*")``/``os.walk()`` so a tier's own nested subdirectories
    (e.g. the top tier's ``base/`` subfolder) never pollute this tier's file
    list -- see the module docstring PITFALL note.
    """
    if not folder.is_dir():
        return []
    results = []
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        for ext in extensions:
            if name.lower().endswith(ext.lower()):
                results.append(entry)
                break
    return results


def _best_match_per_component(files: list[Path]) -> dict[str, ComponentMatch]:
    """Within a single tier's file list, pick the lowest variant index per
    component code. Files that don't match the naming convention are
    skipped with a warning (not a crash) -- e.g. stray .ytd texture files
    that happen to live alongside drawables, or non-conforming names.
    """
    best: dict[str, ComponentMatch] = {}
    for path in files:
        # Strip recognized extensions (longest first, since ".ydd.xml" ends
        # with the same suffix chars as a hypothetical ".xml" alone) before
        # matching the component prefix against the stem.
        stem = path.name
        for ext in sorted(RECOGNIZED_EXTENSIONS, key=len, reverse=True):
            if stem.lower().endswith(ext.lower()):
                stem = stem[: -len(ext)]
                break
        match = _COMPONENT_RE.match(stem)
        if not match:
            logger.warning("skipping file with non-conforming component name: %s", path)
            continue
        component = match.group(1).lower()
        variant_index = int(match.group(2))
        current = best.get(component)
        if current is None or variant_index < current.variant_index:
            best[component] = ComponentMatch(component, variant_index, path)
    return best


def resolve_component_diffuse(drawable_path: Path, ethnicity: str = "whi", letter: str | None = None) -> Path | None:
    """Return the .dds the GAME uses as diffuse for a component drawable,
    or None if it can't be determined from the exported XML tree.

    GTA picks a ped component's diffuse by FILE NAME convention, not by the
    texture name embedded in the drawable's shader: drawable
    ``<comp>_<NNN>_<u|r>`` uses ``<comp>_diff_<NNN>_<letter>_<race>.ytd``
    (letter = texture variation, race = ethnicity or ``uni``). The embedded
    shader name can be stale -- e.g. CatalinaNude's ``uppr_000_r`` is a
    renamed ``uppr_015_r`` still pointing at a nonexistent
    ``uppr_diff_015_a_whi``, and its pubis mesh (``task_001_u``, shipped
    inside ``teef_000_u``) points at a nonexistent ``task_diff_001_a_uni``,
    while ``teef_diff_000_a_uni.ytd`` holds the real texture.

    Variation choice: race == ``ethnicity`` first, then ``uni``, then the
    first in sorted order (lowest letter = default variation); `letter`
    forces that texture variation (e.g. a picked hair colour). The .dds is
    the first ``<FileName>`` of that ``.ytd.xml``, looked up next to it
    (CodeWalker's export dumps embedded textures alongside).
    """
    stem = drawable_path.name
    for ext in sorted(RECOGNIZED_EXTENSIONS, key=len, reverse=True):
        if stem.lower().endswith(ext.lower()):
            stem = stem[: -len(ext)]
            break
    match = _COMPONENT_RE.match(stem)
    if not match:
        return None
    component, variant = match.group(1).lower(), match.group(2)
    folder = drawable_path.parent
    candidates = sorted(folder.glob(f"{component}_diff_{variant}_{letter or '*'}_*.ytd.xml"))
    if not candidates:
        return None

    def race(path: Path) -> str:
        return path.name[: -len(".ytd.xml")].rsplit("_", 1)[-1].lower()

    for wanted in (ethnicity.lower(), "uni"):
        preferred = [p for p in candidates if race(p) == wanted]
        if preferred:
            candidates = preferred
            break
    try:
        file_name = ET.parse(candidates[0]).getroot().findtext(".//FileName")
    except ET.ParseError:
        logger.warning("unparseable texture dictionary: %s", candidates[0])
        return None
    if not file_name:
        return None
    dds = folder / file_name
    return dds if dds.is_file() else None


def resolve_layered_components(ped_root: Path, ped_name: str,
                                extensions: tuple[str, ...] = RECOGNIZED_EXTENSIONS) -> dict[str, Path]:
    """Resolve the winning file per component code across the 3-tier
    base_all / mid / top layered convention described in this module's
    docstring.

    ``ped_root`` is the directory that directly contains ``<ped_name>/``
    (the top tier) and ``<ped_name>.ymt`` (e.g.
    ``peds/template_ped/mp_f_freemode_01_mp_f_bikerdlc_01``'s PARENT, i.e.
    ``peds/template_ped``) -- mirroring the real on-disk layout:

        ped_root/
          <ped_name>.ymt                                  (top .ymt, if present)
          <ped_name>/                                      <- top tier (HIGHEST)
            base/
              <ped_name>.ymt                                (mid .ymt, if present)
              <ped_name>/                                  <- mid tier
              base_all/
                <ped_name>/                                <- base_all tier (LOWEST)

    Returns ``{component_code: winning_file_path}``, one entry per component
    code that is defined in AT LEAST ONE of the three tiers, with ties
    broken by tier priority (top > mid > base_all) and, within a tier, by
    lowest variant index.
    """
    top_dir = ped_root / ped_name
    mid_dir = top_dir / "base" / ped_name
    base_all_dir = top_dir / "base" / "base_all" / ped_name

    tier_dirs = {
        "base_all": base_all_dir,
        "mid": mid_dir,
        "top": top_dir,
    }

    per_tier_best: dict[str, dict[str, ComponentMatch]] = {}
    for tier in TIER_NAMES:
        files = _matching_files(tier_dirs[tier], extensions)
        per_tier_best[tier] = _best_match_per_component(files)

    resolved: dict[str, Path] = {}
    # Apply tiers in priority order, lowest first, so a later (higher
    # priority) tier's entry for the same component code overwrites an
    # earlier one -- this IS the "top beats mid beats base_all" rule.
    for tier in TIER_NAMES:
        for component, match in per_tier_best[tier].items():
            resolved[component] = match.path

    return resolved


def resolve_body_donor_components(donor_dir: Path, allowed_components: tuple[str, ...],
                                   extensions: tuple[str, ...] = RECOGNIZED_EXTENSIONS) -> dict[str, Path]:
    """Resolve components from a cross-ped "body donor" folder -- e.g. a
    COMPLETELY DIFFERENT ped's own extracted component folder (its own
    standalone mod, not part of this ped's base_all/mid/top convention),
    used purely to borrow specific body-shape geometry.

    Added 2026-10-12 per the user's own words (verbatim): they placed files
    "qui ont exactement des mensurations (bustes, jambes+fessier)" under
    `peds/<ped_name>/alt/<donor_name>/` and asked to assemble them with the
    base ped "en procédant par élimination" -- meaning: use the donor's
    file for a component code ONLY if that code is in `allowed_components`,
    even if the donor folder defines other codes too (a donor is typically
    a full standalone ped mod with its own head/hair/outfit, e.g. the real
    `CatalinaNude`/`Beachgirl5` donors tested against this project: both
    ship uppr+lowr+head+hair+hand+feet+decl, but we only ever want their
    uppr/lowr here -- head/hair/outfit pieces must keep coming from the
    ped's own normal layers, a body donor is NOT an identity swap).

    Verified for real (2026-10-12) against two actual donor peds
    (`alt/BeachGirl5/CatalinaNude/`, `alt/BeachGirl5/Beachgirl5/Beachgirl5/`):
    both use the exact same universal GTA human skeleton (same bone names
    down to SPR_L/R_Breast), and their uppr/lowr meshes import and align
    at the correct scale/position against this project's own borrowed
    skeleton with zero offset -- confirmed by a real Blender render
    (overlaid cleanly on the base ped, no floating/misplaced/wrong-scale
    geometry) -- see docs/architecture-decisions.md for the full writeup.

    Returns `{component_code: path}`, restricted to `allowed_components`
    (silently ignoring any other codes the donor folder happens to also
    define -- that's the "elimination" part: we pick what we want FROM the
    donor, we don't adopt the donor wholesale).
    """
    files = _matching_files(donor_dir, extensions)
    best = _best_match_per_component(files)
    return {component: match.path for component, match in best.items() if component in allowed_components}


def resolve_layered_components_with_body_donor(
    ped_root: Path, ped_name: str, donor_dir: Path,
    allowed_donor_components: tuple[str, ...] = ("uppr", "lowr"),
    extensions: tuple[str, ...] = RECOGNIZED_EXTENSIONS,
) -> dict[str, Path]:
    """resolve_layered_components() PLUS an extra, even-higher-priority
    "body donor" override for body-shape-only components (see
    resolve_body_donor_components's docstring for the full rationale).

    Anything the donor does NOT provide (either because it genuinely lacks
    that component, or because `allowed_donor_components` deliberately
    excludes it) falls back to this ped's own normal background layer
    (top > mid > base_all) -- matching the user's own words (verbatim,
    2026-10-12): "aller superposer les éléments qui ne se trouvent pas
    dans un calque dans un calque inférieur, d'arrière-plan".
    """
    resolved = resolve_layered_components(ped_root, ped_name, extensions)
    donor_resolved = resolve_body_donor_components(donor_dir, allowed_donor_components, extensions)
    resolved.update(donor_resolved)  # donor always wins for its allowed codes
    return resolved


# --- Skeleton compatibility ---------------------------------------------------
# A component .ydd.xml carries skin indices but no skeleton. Each drawable
# model declares the size of its bone-matrix palette (<Unknown1 value="N"/>
# = byte 0 of CodeWalker's SkeletonBinding, the only use of that tag in a
# .ydd.xml); its <BoneIDs> map palette slots to skeleton bone indices. The
# shipped mp_f_freemode_01.yft (verified 2026-10-12: internal name
# pack:/mp_f_freemode_01, byte-identical to CatalinaNude.yft) has 128 bones
# with 21 facial bones FB_*_000 at 103-123 and RB_Neck_1, SPR_L/R_Breast,
# IK_Root at 124-127. MP Freemode DLC components (e.g.
# mp_f_freemode_01_female_freemode_beach) declare a 107-slot identity
# palette = that skeleton WITHOUT the facial bones (128 - 21): their
# index-103 vertices sit on the back of the neck, 5.8 cm from RB_Neck_1 and
# 11 cm from FB_L_Brow_Out_000, the bone index 103 names on the real
# skeleton. Imported as-is, that hair/collar would follow an eyebrow. How
# the game itself resolves those slots is not verified.

_DECLARED_BONE_COUNT_RE = re.compile(r'<Unknown1 value="(\d+)"')
_INSERTED_FACIAL_BONE_RE = re.compile(r"^FB_.+_000$")


def skeleton_bone_names(yft_xml: Path) -> list[str]:
    """Bone names of a CodeWalker-XML .yft's skeleton, in index order."""
    bones = ET.parse(yft_xml).getroot().find(".//Skeleton/Bones")
    if bones is None:
        raise ValueError(f"no <Skeleton><Bones> in {yft_xml}")
    return [item.findtext("Name", "") for item in bones.findall("Item")]


def declared_bone_counts(ydd_xml: Path) -> set[int]:
    """Skeleton bone counts declared by the drawable models of a .ydd.xml."""
    return {int(n) for n in _DECLARED_BONE_COUNT_RE.findall(
        ydd_xml.read_text(encoding="utf-8", errors="ignore"))}


def bone_group_remap(skeleton_names: list[str], component_bone_count: int) -> dict[str, str]:
    """Vertex-group renames that put the skin weights of a component with a
    `component_bone_count`-slot palette back on the right bones once
    imported against `skeleton_names` (Sollumz names each group after
    skeleton_names[index]): {wrong group: right bone}, {} when the counts
    match. The component's bone order is taken to be `skeleton_names`
    without the FB_*_000 facial bones; ValueError if that doesn't give
    exactly `component_bone_count` bones (unknown layout -- don't guess)."""
    if component_bone_count == len(skeleton_names):
        return {}
    base = [name for name in skeleton_names if not _INSERTED_FACIAL_BONE_RE.match(name)]
    if len(base) != component_bone_count:
        raise ValueError(f"component built for {component_bone_count} bones, skeleton has "
                         f"{len(skeleton_names)} ({len(base)} without FB_*_000): unknown bone layout")
    return {skeleton_names[i]: base[i] for i in range(component_bone_count)
            if skeleton_names[i] != base[i]}


# Tokens per vertex element in a CodeWalker-XML <VertexBuffer><Data2> line.
LAYOUT_SIZES = {"Position": 3, "BlendWeights": 4, "BlendIndices": 4, "Normal": 3,
                "Colour0": 4, "Colour1": 4, "Tangent": 4, "Binormal": 4,
                **{f"TexCoord{i}": 2 for i in range(8)}}


def high_lod_positions(ydd_root: ET.Element) -> list[tuple[float, float, float]]:
    """Vertex positions of a parsed .ydd.xml's High-LOD geometries (the LOD
    Blender imports), in file order."""
    out = []
    for geometry in ydd_root.iterfind("Item/DrawableModelsHigh/Item/Geometries/Item"):
        vb = geometry.find("VertexBuffer")
        layout = [el.tag for el in vb.find("Layout")]
        at = sum(LAYOUT_SIZES[tag] for tag in layout[:layout.index("Position")])
        data = vb.find("Data2") if vb.find("Data2") is not None else vb.find("Data")
        out += [tuple(float(t) for t in line.split()[at:at + 3]) for line in data.text.split("\n") if line.strip()]
    return out


def component_variants(drawable: Path) -> list[Path]:
    """Every drawable variant of `drawable`'s component in its folder (same
    extension), by variant index: the drawables the ped's .ymt can pick for
    that slot (CatalinaNude: uppr_000..005, lowr_000..002 -- 'bodyshapes')."""
    def parse(path):
        """(component, variant index, extension) or None."""
        ext = next((e for e in sorted(RECOGNIZED_EXTENSIONS, key=len, reverse=True)
                    if path.name.lower().endswith(e)), None)
        match = ext and _COMPONENT_RE.match(path.name[:-len(ext)])
        return (match.group(1).lower(), int(match.group(2)), ext) if match else None

    own = parse(drawable)
    if own is None:
        raise ValueError(f"not a component drawable name: {drawable.name}")
    found = sorted((info[1], p) for p, info in ((p, parse(p)) for p in _matching_files(drawable.parent, (own[2],)))
                   if info and info[0] == own[0])
    return [p for _i, p in found]


def skin_variant_combos(variants: dict[str, list], seamed) -> list[dict]:
    """Which uppr/lowr pairs to build so every skin variant is fitted once.
    `variants` = {"uppr": [...], "lowr": [...]} (by variant index, the
    lowest first); `seamed(uppr, lowr)` -> True if the two meshes share
    their waist seam (welded vertices). The first combo is the lowest pair
    (the main build); then every other variant once, paired with the lowest
    variant of the other component it shares a seam with -- or the lowest
    one if none does. Pairs found from both sides appear once."""
    combos = [{"uppr": variants["uppr"][0], "lowr": variants["lowr"][0]}]
    for component, other in (("uppr", "lowr"), ("lowr", "uppr")):
        for variant in variants[component][1:]:
            def shares(candidate):
                pair = {component: variant, other: candidate}
                return seamed(pair["uppr"], pair["lowr"])
            partner = next((c for c in variants[other] if shares(c)), variants[other][0])
            combo = {component: variant, other: partner}
            if combo not in combos:
                combos.append(combo)
    return combos


# TODO(integration): wire resolve_layered_components() into the pipeline step
# that runs just before Sollumz import, e.g. a new --layered-ped-root /
# --ped-name pair of CLI args on scripts/blender_rig_export.py's main(), or a
# small import-time helper called from there, so the resolved per-component
# file dict feeds the Sollumz import call instead of a single hardcoded mesh
# path. Not wired yet: this task only makes the selection logic itself
# permanent and tested (see docs/architecture-decisions.md).
