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
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Component code prefixes recognized by the real CPedVariationInfo convention,
# matched case-insensitively at the start of the filename, followed by an
# underscore and a numeric variant index: e.g. "jbib_000_u.ydd",
# "accs_diff_000_a_uni.ytd" (component "accs", we don't need the rest).
_COMPONENT_RE = re.compile(
    r"^(jbib|lowr|hand|accs|decl|uppr|feet|hair|berd|task)_(\d+)",
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


# TODO(integration): wire resolve_layered_components() into the pipeline step
# that runs just before Sollumz import, e.g. a new --layered-ped-root /
# --ped-name pair of CLI args on scripts/blender_rig_export.py's main(), or a
# small import-time helper called from there, so the resolved per-component
# file dict feeds the Sollumz import call instead of a single hardcoded mesh
# path. Not wired yet: this task only makes the selection logic itself
# permanent and tested (see docs/architecture-decisions.md).
