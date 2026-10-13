"""Full layered-ped construction pipeline, run headless via:

    blender --background --python scripts/import_layered_ped.py -- \\
        --template templates/template_female_a_f_y_business_02.blend \\
        --xml-root /path/to/exported/xml/tree \\
        --ped-root /path/to/exported/xml/tree/<ped_name_parent> \\
        --ped-name mp_f_freemode_01_mp_f_bikerdlc_01 \\
        --output-blend workspace/mp_f_freemode_01_mp_f_bikerdlc_01.blend \\
        [--preview-png workspace/preview.png]

Ties together, as permanent code, the three pieces proven separately this
session (2026-10-12):
  1. layered_ped_builder.resolve_layered_components() -- picks the winning
     file per component code across the base_all/mid/top tiers (top wins,
     per the user's explicit rule: "le niveau le plus haut doit etre le
     rendu final dans gta").
  2. Sollumz's bpy.ops.sollumz.import_assets() onto an existing template's
     skeleton (this asset family has no .yft/skeleton of its own) +
     blender_rig_export.rig_unskinned_body_components() (only the
     Fragment's own Drawable comes in pre-skinned; everything else needs
     auto-weighting).
  3. blender_rig_export.relink_external_textures() -- Sollumz imports each
     Image with a RELATIVE path that never resolves against the real
     exported-XML directory tree; without this every component renders in
     a flat, undetailed color instead of its real diffuse texture.

The input to this script is the CodeWalker-XML form (.ydd.xml), NOT raw
game binaries -- run scripts/codewalker_convert.export_tree_to_xml() first
to produce --xml-root from the user's raw peds/<ped_name>/ extraction.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_rig_export as bre  # noqa: E402
import body_morphology  # noqa: E402
from layered_ped_builder import (  # noqa: E402
    bone_group_remap,
    component_variants,
    declared_bone_counts,
    high_lod_positions,
    resolve_layered_components,
    resolve_component_diffuse,
    resolve_layered_components_with_body_donor,
    skeleton_bone_names,
    skin_variant_combos,
)


def parse_args():
    raw_args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Layered ped construction (base_all/mid/top)")
    parser.add_argument("--template", required=True, help="Path to base ped template .blend "
                                                            "(provides the skeleton this asset family lacks)")
    parser.add_argument("--ped-root", required=True, help="Directory directly containing <ped-name>/ "
                                                            "and <ped-name>.ymt -- see "
                                                            "layered_ped_builder's module docstring for the exact layout")
    parser.add_argument("--ped-name", required=True)
    parser.add_argument("--output-blend", required=True, help="Where to save the assembled .blend")
    parser.add_argument("--preview-png", default=None, help="Optional: also render a quick preview PNG")
    parser.add_argument("--body-donor-root", default=None,
                         help="Optional: directory of an already-exported-to-XML cross-ped "
                              "'body donor' (e.g. a nude reference ped chosen for its exact "
                              "bust/leg/hip measurements) whose uppr/lowr override this ped's "
                              "own layers -- see layered_ped_builder.resolve_body_donor_components")
    parser.add_argument("--body-donor-components", default="uppr,lowr",
                         help="Comma-separated component codes to take FROM the body donor "
                              "(default: uppr,lowr -- everything else always comes from this "
                              "ped's own normal layers, never the donor)")
    parser.add_argument("--extra-texture-roots", default=None,
                         help="Comma-separated extra directories to search for .dds textures "
                              "(e.g. a separate skin-texture-only folder not nested under "
                              "--ped-root or --body-donor-root) -- each gets its own additive "
                              "relink_external_textures() pass")
    parser.add_argument("--generic-skin-root", default=None,
                         help="Optional: directory of standard per-ethnicity skin textures "
                              "(mp_fm_skin_f_{up,lo}_{ethnicity}.dds) to swap onto the body "
                              "donor's uppr/lowr, decoupling the borrowed body SHAPE from the "
                              "donor's own bespoke skin TONE -- makes the donor reusable as a "
                              "generic nude base. Requires --body-donor-root.")
    parser.add_argument("--ethnicity", "--generic-skin-ethnicity", dest="ethnicity", default="whi",
                         help="Ethnicity code (ara/bla/chi/lat/pak/whi, default: whi): picks each "
                              "component's <comp>_diff_<NNN>_<letter>_<race>.ytd texture variation "
                              "and the --generic-skin-root file")
    parser.add_argument("--morphs", default=None,
                         help="Optional body morphology sliders applied after assembly, e.g. "
                              "'bust_size=1.2,hip_width=1.1' -- see body_morphology.MORPH_SLIDERS "
                              f"for valid names (values clamped to "
                              f"[{body_morphology.SLIDER_MIN}, {body_morphology.SLIDER_MAX}])")
    parser.add_argument("--bust-size", default=None,
                         help="Optional EU bra size to fit the bust to, e.g. '90C' or just 'B' (keeps the "
                              "body's own band): measured on the mesh; a different band widens or "
                              "narrows the ribcage until the underbust matches, then the bust is "
                              "reshaped until its circumference matches (body_morphology.fit_bust)")
    parser.add_argument("--glutes", default=None, choices=sorted(body_morphology.GLUTE_SHAPES),
                         help="Optional buttock shape: measured on the mesh and reshaped until the "
                              "lateral (projection) and posterior (hip width) waist-to-hip ratios "
                              "match body_morphology.GLUTE_SHAPES (body_morphology.fit_glutes)")
    parser.add_argument("--glute-height", default=None, choices=sorted(body_morphology.GLUTE_HEIGHTS),
                         help="Optional height of the buttocks' most prominent point between the "
                              "top of the intergluteal cleft and the gluteal fold "
                              "(body_morphology.GLUTE_HEIGHTS)")
    parser.add_argument("--deformation-out", default=None,
                        help="Write the applied --bust-size/--glutes reshapes as JSON (with the "
                             "component files they were fitted on) for scripts/ped_package.py")
    parser.add_argument("--skeleton-yft", default=None,
                         help="CodeWalker-XML .yft whose skeleton is used for a ped that ships "
                              "without its own <ped-name>.yft.xml (e.g. an MP Freemode component "
                              "pack): must match the --template skeleton")
    return parser.parse_args(raw_args)


def _external_skeleton_import_kwargs(skeleton_yft: Path) -> dict:
    """sollumz.import_assets kwargs that use `skeleton_yft` as the .ydd's
    external skeleton. use_custom_settings=True makes Sollumz read EVERY
    import setting from the operator instead of the user's preferences, so
    the user's current preference values are copied over first (texture
    mode etc. must not silently change); writing the preferences instead
    would persist to the user's Blender config."""
    prefs = next(addon.preferences for key, addon in bpy.context.preferences.addons.items()
                 if "sollumz" in key.lower()).import_settings
    op_props = bpy.ops.sollumz.import_assets.get_rna_type().properties.keys()
    kwargs = {
        prop.identifier: getattr(prefs, prop.identifier)
        for prop in prefs.bl_rna.properties
        if prop.identifier in op_props and prop.identifier != "rna_type"
    }
    kwargs.update(use_custom_settings=True,
                  dwd_import_external_skeleton="SAVED",
                  dwd_import_external_skeleton_saved_path=str(skeleton_yft.resolve()))
    return kwargs


def build_layered_ped(template_path: str, ped_root: str, ped_name: str,
                       body_donor_root: str | None = None,
                       body_donor_components: tuple[str, ...] = ("uppr", "lowr"),
                       extra_texture_roots: tuple[str, ...] = (),
                       generic_skin_root: str | None = None,
                       ethnicity: str = "whi",
                       fallback_skeleton_yft: str | None = None,
                       component_files: dict | None = None) -> dict:
    """The bpy-dependent orchestration: resolve layers, import, rig, relink
    textures. Returns a stats dict for the caller to report/log. Split out
    from main() so a test can call it with a fake bpy and assert on the
    call sequence without needing a real Blender process.

    `component_files` ({component: drawable path}) replaces the resolved
    file of those components -- e.g. another body variant (uppr_003).

    `body_donor_root`, when given, is a directory of already-exported
    CodeWalker-XML files (.ydd.xml) for a DIFFERENT ped chosen purely for
    its body-shape geometry (bust/legs/hips) -- see
    layered_ped_builder.resolve_layered_components_with_body_donor() for
    the full "by elimination" rule (only `body_donor_components` codes are
    taken from it, everything else still comes from this ped's own layers).
    """
    if body_donor_root:
        resolved = resolve_layered_components_with_body_donor(
            Path(ped_root), ped_name, Path(body_donor_root),
            allowed_donor_components=tuple(body_donor_components),
            extensions=(".ydd.xml",),
        )
    else:
        resolved = resolve_layered_components(Path(ped_root), ped_name,
                                               extensions=(".ydd.xml",))
    resolved.update({comp: Path(p) for comp, p in (component_files or {}).items()})
    stats: dict = {"resolved_components": {comp: str(p) for comp, p in resolved.items()}}

    bpy.ops.wm.open_mainfile(filepath=template_path)
    for module in ("bl_ext.user_default.sollumz", "sollumz"):
        try:
            bpy.ops.preferences.addon_enable(module=module)
            break
        except Exception:
            continue

    armature = bre.find_reference_armature(bpy.data.objects)
    stats["armature"] = armature.name if armature else None
    if armature is None:
        stats["error"] = "Template lacks a skeleton armature."
        return stats

    # The component .ydd files are natively skinned (BlendIndices/
    # BlendWeights) but carry no skeleton: without one, Sollumz drops that
    # skinning and we'd have to fall back to heat auto-weights, which fail
    # silently on these meshes ("Bone Heat Weighting: failed to find
    # solution", every vertex left at weight 0 -- found 2026-10-12: morphs
    # and animations had NO effect on the body). The ped's own <ped>.yft
    # (GTA layout: next to the <ped>/ folder) is that skeleton; a ped
    # shipped without one (e.g. an MP Freemode DLC component pack) uses
    # `fallback_skeleton_yft` -- the skeleton matching the template.
    own_skeleton = Path(ped_root) / f"{ped_name}.yft.xml"
    skeleton_yft = own_skeleton if own_skeleton.is_file() else \
        Path(fallback_skeleton_yft) if fallback_skeleton_yft else None
    if skeleton_yft is not None and not skeleton_yft.is_file():
        stats["error"] = f"fallback skeleton not found: {skeleton_yft}"
        return stats
    import_kwargs = _external_skeleton_import_kwargs(skeleton_yft) if skeleton_yft else {}
    stats["skeleton_yft"] = str(skeleton_yft) if skeleton_yft else None
    stats["skeleton_is_fallback"] = skeleton_yft is not None and skeleton_yft is not own_skeleton

    before_objs = set(bpy.data.objects)
    failed_imports = []
    new_meshes_by_component: dict[str, list] = {}
    for component, path in resolved.items():
        before_this_import = set(bpy.data.objects)
        try:
            bpy.ops.sollumz.import_assets(directory=str(path.parent) + "/",
                                           files=[{"name": path.name}], **import_kwargs)
        except Exception as exc:
            failed_imports.append((component, str(exc)))
            continue
        new_meshes_by_component[component] = [
            o for o in (set(bpy.data.objects) - before_this_import) if o.type == 'MESH'
        ]
        for obj in new_meshes_by_component[component]:
            obj[body_morphology.PED_COMPONENT_PROP] = component
    stats["failed_imports"] = failed_imports

    # Components whose bone palette uses the facial-less bone order (MP
    # Freemode DLC: 107 slots) imported against the full skeleton get their
    # out-of-place indices' weights moved back to the right bones -- see
    # layered_ped_builder.bone_group_remap().
    stats["bone_remaps"], stats["bone_remap_errors"] = {}, {}
    skeleton_names = None
    for component, meshes in new_meshes_by_component.items():
        counts = declared_bone_counts(resolved[component])
        if skeleton_yft is None or not counts:
            continue
        if skeleton_names is None:
            skeleton_names = skeleton_bone_names(skeleton_yft)
        if counts == {len(skeleton_names)}:
            continue
        if len(counts) != 1:
            stats["bone_remap_errors"][component] = f"mixed declared bone counts {sorted(counts)}"
            continue
        try:
            mapping = bone_group_remap(skeleton_names, counts.pop())
        except ValueError as exc:
            stats["bone_remap_errors"][component] = str(exc)
            continue
        stats["bone_remaps"][component] = bre.remap_vertex_groups(meshes, mapping)

    new_meshes = [o for o in (set(bpy.data.objects) - before_objs) if o.type == 'MESH']
    stats["imported_mesh_count"] = len(new_meshes)

    # Sollumz gives each external-skeleton import its own copy of the
    # armature; same bones (names/order/rest pose) as the template's, so
    # re-point the meshes at our working armature.
    bre.bind_meshes_to_armature(armature, new_meshes)
    rigged = bre.rig_unskinned_body_components(armature, new_meshes)
    stats["rigged_count"] = len(rigged)
    stats["unweighted_meshes"] = bre.meshes_without_weights(new_meshes)

    relink_result = bre.relink_external_textures(ped_root)
    extra_roots = list(extra_texture_roots)
    if body_donor_root:
        extra_roots.append(body_donor_root)
    for root in extra_roots:
        # Each extra root lives in a SEPARATE directory tree (not nested
        # under ped_root) -- an additive pass picks up whatever earlier
        # passes didn't find. relink_external_textures() re-walks and
        # re-matches by basename each call, so calling it repeatedly is
        # safe (harmless redundant work for already-relinked images,
        # genuinely new matches for this root's own textures).
        pass_result = bre.relink_external_textures(root)
        relink_result["relinked"] = list(set(relink_result["relinked"]) | set(pass_result["relinked"]))
        relink_result["not_found"] = [n for n in relink_result["not_found"] if n in pass_result["not_found"]]
    stats["textures_relinked"] = len(relink_result["relinked"])
    stats["textures_not_found"] = relink_result["not_found"]

    # Fill each component's UNRESOLVED diffuse the way the game names it
    # (<comp>_diff_<NNN>_* .ytd), since the texture name embedded in the
    # drawable's shader can be stale -- see
    # layered_ped_builder.resolve_component_diffuse(). Diffuses that already
    # resolve to a real file are kept (only_missing): whether the game also
    # overrides those can't be verified offline.
    component_diffuse = {}
    for component, meshes in new_meshes_by_component.items():
        dds = resolve_component_diffuse(resolved[component], ethnicity)
        if dds is None or not meshes:
            continue
        replaced = bre.apply_diffuse_texture(meshes, str(dds), only_missing=True)
        if replaced:
            component_diffuse[component] = dds.name
            stats["textures_not_found"] = [n for n in stats["textures_not_found"] if n not in replaced]
    stats["component_diffuse"] = component_diffuse

    if generic_skin_root:
        # Decouples the body donor's borrowed SHAPE from its own bespoke
        # skin TONE -- see blender_rig_export.apply_generic_skin_textures()
        # for the full rationale (makes the donor reusable as a generic
        # nude base across different peds, not pinned to one donor's
        # identity texture). Only meshes actually resolved to "uppr"/"lowr"
        # for THIS build are eligible -- if the donor didn't win those
        # codes (e.g. body_donor_root wasn't given), there's nothing to
        # swap and this is silently a no-op.
        body_objects = {}
        for component in ("uppr", "lowr"):
            meshes = new_meshes_by_component.get(component, [])
            if meshes:
                body_objects[component] = meshes[0]
        skin_swap_result = bre.apply_generic_skin_textures(
            body_objects, generic_skin_root, ethnicity=ethnicity)
        stats["generic_skin_swapped"] = skin_swap_result["swapped"]
        stats["generic_skin_not_found"] = skin_swap_result["not_found"]

    # Hide the template's own original body so only the new components show.
    #
    # PITFALL found 2026-10-12 while investigating a visually "doubled"
    # body in templates/template_mp_f_freemode_01_nude_base.blend: TWO
    # fixes were tried and both failed before this one.
    #   1. Iterating `armature.children` (Outliner parent-child hierarchy)
    #      missed meshes bound via an Armature MODIFIER with no Object
    #      parenting.
    #   2. Checking "has an Armature modifier pointing at `armature`, OR
    #      is a child of `armature`" STILL missed them: inspection showed
    #      templates/template_female_a_f_y_business_02.blend's own
    #      pre-existing body meshes (from earlier session work, baked
    #      permanently into that .blend) sit under a COMPLETELY SEPARATE,
    #      disconnected parent chain (hash_X.model -> an Empty -> a
    #      DIFFERENT empty object, "a_f_y_business_02.001" -- note the
    #      ".001", not the real armature) with NO modifiers at all. They
    #      just happen to visually overlap because they're the same ped's
    #      geometry at the same position -- there is no clean programmatic
    #      "skinned to our armature" relationship to detect at all.
    #   Real fix: this function's contract is that `template_path` ONLY
    #   ever contributes a skeleton, nothing else -- so ANY mesh/empty that
    #   existed in the scene BEFORE this run's own imports (tracked by
    #   `before_objs`, already computed above) is leftover clutter and must
    #   always be hidden, full stop, regardless of how (or whether) it's
    #   bound to anything.
    for obj in before_objs:
        if obj.type in ('MESH', 'EMPTY'):
            obj.hide_render = True
            obj.hide_set(True)
    # Hide known floor/backdrop junk (flat, large-footprint meshes -- found
    # 2026-10-12 occluding the upper body in a real render, see
    # docs/architecture-decisions.md).
    for obj in list(bpy.data.objects):
        if obj.type != 'MESH' or obj in new_meshes:
            continue
        dims = obj.dimensions
        if dims.z < 0.3 and (dims.x > 2.0 or dims.y > 2.0):
            obj.hide_render = True
            obj.hide_set(True)

    # Hide stray duplicate ARMATURE objects -- found 2026-10-12: the user
    # reported the skeleton "didn't look right" when inspecting
    # templates/template_nude_generic.blend interactively in Blender.
    # Root cause: some resolved components embed their OWN trivial/
    # placeholder skeleton in their .ydd.xml (the same ones that produce
    # the "SKEL_ROOT.001"/".002" floor-junk meshes just hidden above), and
    # Sollumz's importer creates a brand-new Armature object for each one
    # rather than reusing our existing `armature`. Those duplicate
    # skeletons deform nothing visible (their only mesh is the floor junk,
    # already hidden) but the ARMATURE OBJECTS THEMSELVES were never
    # hidden, so Blender drew THREE full 104-bone skeletons overlapping at
    # the same position -- confirmed by inspecting the real scene (3
    # armature objects, all with hide_render=False, only one of which
    # -- `armature` itself -- actually deforms a visible mesh). Any
    # Armature object other than our own working `armature` is always
    # junk by this function's contract (same reasoning as the mesh/empty
    # hide step above) and must be hidden too.
    for obj in list(bpy.data.objects):
        if obj.type == 'ARMATURE' and obj is not armature:
            obj.hide_render = True
            obj.hide_set(True)

    stats["new_meshes"] = new_meshes
    return stats


def _frame_camera_on(objects, lens=35.0):
    import mathutils
    all_corners = []
    for o in objects:
        if o.hide_render:
            continue
        all_corners.extend(o.matrix_world @ mathutils.Vector(c) for c in o.bound_box)
    if not all_corners:
        center, height = mathutils.Vector((0, 0, 1)), 1.8
    else:
        xs = [c[0] for c in all_corners]
        ys = [c[1] for c in all_corners]
        zs = [c[2] for c in all_corners]
        center = mathutils.Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
        height = max(zs) - min(zs)

    cam_data = bpy.data.cameras.new("preview_cam")
    cam_data.lens = lens
    cam_obj = bpy.data.objects.new("preview_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    dist = max(height * 2.0, 2.0)
    cam_obj.location = (center.x, center.y - dist, center.z)
    cam_obj.rotation_euler = (center - cam_obj.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = cam_obj


def render_preview(new_meshes, out_png: str):
    import mathutils  # noqa: F401  (re-imported for clarity at call site)
    if not any(o.type == 'LIGHT' for o in bpy.data.objects):
        light_data = bpy.data.lights.new("preview_light", type='SUN')
        light_data.energy = 3.0
        light_obj = bpy.data.objects.new("preview_light", light_data)
        bpy.context.scene.collection.objects.link(light_obj)
        light_obj.rotation_euler = (0.9, 0.0, 0.6)
    if bpy.context.scene.world is None:
        # A scene built from bpy.ops.wm.read_factory_settings(use_empty=True)
        # (e.g. a fresh native-skeleton template built straight from a
        # donor's own .yft, with no pre-existing business_02-style .blend
        # to inherit a World from) has NO World datablock at all -- found
        # 2026-10-12 as a real crash ('NoneType' object has no attribute
        # 'use_nodes') when building templates/template_nude_generic.blend
        # from templates/template_beachgirl5_native.blend for the first
        # time. Create one on demand rather than assuming it always exists.
        bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.world.use_nodes = True
    bg = bpy.context.scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.08, 0.08, 0.09, 1.0)
        bg.inputs[1].default_value = 1.0

    _frame_camera_on(new_meshes)

    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.render.resolution_x = 900
    bpy.context.scene.render.resolution_y = 1200
    bpy.context.scene.cycles.samples = 64
    bpy.context.scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)


def _hips_line(m) -> str:
    return (f"waist {m['waist_cm']} cm, hips {m['hips_cm']} cm; profile depth {m['waist_depth_cm']}/"
            f"{m['hip_depth_cm']} cm = lateral waist-to-hip {m['lateral_whr']}; width "
            f"{m['waist_width_cm']}/{m['hip_width_cm']} cm = posterior waist-to-hip {m['posterior_whr']} "
            f"(~{m['shape']}); apex at {m['apex_position']} of cleft-to-fold (~{m['height']})")


SEAM_MIN_VERTICES = 8  # welded vertices for two skin variants to count as sharing their waist seam


def fit_body_variants(args, build_kwargs, main_stats, glute_fit) -> list[dict]:
    """bpy: the same body targets on every OTHER drawable variant of the
    skin components (the .ymt picks among uppr_000..005 / lowr_000..002 in
    game; only the lowest pair was built). Each variant is rebuilt with its
    seam partner (layered_ped_builder.skin_variant_combos) and:
    - the bust is fitted again on it: the variants' breasts differ (80D to
      80>H on CatalinaNude) and replaying the main fit missed 90C by -8.5
      to +5.1 cm; the bust field stays far above the waist seam;
    - the glute field, which crosses the seam, is the main one replayed
      (replay_glutes) on every variant welded to a main file -- any uppr is
      worn with any lowr, one field keeps every seam closed -- and fitted
      again only on a pair welded to neither (its own body).
    Returns one entry per variant file: {"component", "xml", "built_with",
    "deformations" (bake order), "bust", "glutes", "reached"}; a variant
    whose fit failed is left out (shipped unreshaped, ped_package warns)."""
    skin = body_morphology.SKIN_COMPONENTS
    main = {c: Path(main_stats["resolved_components"].get(c, "")) for c in skin}
    top_tier = (Path(args.ped_root) / args.ped_name).resolve()
    if args.body_donor_root or any(p.parent.resolve() != top_tier for p in main.values()):
        print(f"[INFO] body variants: {[str(p) for p in main.values()]} not all {args.ped_name}'s own "
              f"drawables, other variants not fitted")
        return []
    variants = {c: component_variants(main[c]) for c in skin}
    positions = {p: high_lod_positions(ET.parse(p).getroot()) for paths in variants.values() for p in paths}
    combos = skin_variant_combos(variants, lambda u, lo: len(body_morphology.shared_vertices(
        positions[u], positions[lo])) >= SEAM_MIN_VERTICES)
    out = []
    for combo in combos[1:]:
        names = " + ".join(combo[c].name[:-len(".ydd.xml")] for c in skin)
        stats = build_layered_ped(args.template, args.ped_root, args.ped_name, component_files=combo,
                                  **build_kwargs)
        if stats.get("error") or stats["failed_imports"]:
            print(f"[WARN] variant {names}: build failed ({stats.get('error') or stats['failed_imports']}), "
                  f"not reshaped")
            continue
        armature = bpy.data.objects[stats["armature"]]
        deformations, report = [], {}
        if args.bust_size:
            fit = body_morphology.fit_bust(armature, args.bust_size)
            if "error" in fit:
                print(f"[WARN] variant {names}: bust fit failed ({fit['error']}), not reshaped")
                continue
            deformations.append(fit["deformation"])
            report["bust"] = {**fit["result"], "target_bust_cm": fit["target_bust_cm"], "reached": fit["reached"]}
        if glute_fit is not None:
            if any(combo[c] == main[c] for c in skin):
                fit = body_morphology.replay_glutes(armature, glute_fit["deformation"], glute_fit["targets"])
                fit["deformation"], how = glute_fit["deformation"], "main fit replayed"
            else:
                fit = body_morphology.fit_glutes(armature, args.glutes, args.glute_height)
                how = "fitted on this pair"
            if "error" in fit:
                print(f"[WARN] variant {names}: glutes failed ({fit['error']}), not reshaped")
                continue
            deformations.append(fit["deformation"])
            report["glutes"] = {"how": how, "targets": glute_fit["targets"], "result": fit["result"],
                                "reached": fit["reached"]}
        reached = all(r["reached"] for r in report.values())
        bust, glutes = report.get("bust"), report.get("glutes")
        print(f"[VARIANT] {names}: "
              + (f"bust {bust['underbust_cm']}/{bust['bust_cm']} cm (~{bust['size']}, target "
                 f"{bust['target_bust_cm']}); " if bust else "")
              + (f"glutes {glutes['how']}: " + (_hips_line(glutes["result"]) if glutes["result"] else
                                                "not measurable") if glutes else ""))
        if not reached:
            print(f"[WARN] variant {names} misses the targets: "
                  + "; ".join(f"{k} {r.get('target_bust_cm', r.get('targets'))}" for k, r in report.items()
                              if not r["reached"]))
        for c in skin:
            if combo[c] != main[c] and os.path.abspath(combo[c]) not in {v["xml"] for v in out}:
                out.append({"component": c, "xml": os.path.abspath(combo[c]),
                            "built_with": os.path.abspath(combo[[o for o in skin if o != c][0]]),
                            "deformations": [d.to_dict() for d in deformations], **report, "reached": reached})
    return out


def main():
    args = parse_args()
    sliders = body_morphology.parse_slider_spec(args.morphs) if args.morphs else {}
    if args.bust_size:
        body_morphology.parse_bra_size(args.bust_size)  # fail before the (long) build on a typo
    if sliders and args.deformation_out:
        # bone-slider morphs are baked through the Armature modifier, not a
        # replayable Deformation: the export would silently miss them
        sys.exit("[FAIL] --morphs can't be exported (--deformation-out): use --bust-size/--glutes")
    donor_components = tuple(c.strip() for c in args.body_donor_components.split(",") if c.strip())
    extra_roots = tuple(r.strip() for r in args.extra_texture_roots.split(",") if r.strip()) \
        if args.extra_texture_roots else ()
    build_kwargs = dict(body_donor_root=args.body_donor_root, body_donor_components=donor_components,
                        extra_texture_roots=extra_roots, generic_skin_root=args.generic_skin_root,
                        ethnicity=args.ethnicity, fallback_skeleton_yft=args.skeleton_yft)
    stats = build_layered_ped(args.template, args.ped_root, args.ped_name, **build_kwargs)

    print("[INFO] Resolved components:")
    for comp, path in stats.get("resolved_components", {}).items():
        print(f"  - {comp}: {path}")
    if stats.get("error"):
        print(f"[FAIL] {stats['error']}")
        sys.exit(1)
    print(f"[INFO] armature={stats['armature']} imported={stats['imported_mesh_count']} "
          f"rigged={stats['rigged_count']} textures_relinked={stats['textures_relinked']}")
    print(f"[INFO] missing diffuse filled by .ytd convention: {stats['component_diffuse']}")
    if not stats["skeleton_yft"]:
        print(f"[WARN] no {args.ped_name}.yft.xml in {args.ped_root} and no --skeleton-yft: native "
              f".ydd skinning lost, fell back to heat auto-weights")
    elif stats["skeleton_is_fallback"]:
        print(f"[INFO] no {args.ped_name}.yft.xml, using fallback skeleton {args.skeleton_yft}")
    if stats["bone_remaps"]:
        print(f"[INFO] skin weights moved to the right bones (vertices per component): {stats['bone_remaps']}")
    for component, err in stats["bone_remap_errors"].items():
        print(f"[WARN] component '{component}' was built for another skeleton, weights NOT fixed: {err}")
    if stats["unweighted_meshes"]:
        print(f"[WARN] meshes with NO vertex weights (won't follow the skeleton, morphs or "
              f"animations): {stats['unweighted_meshes']}")
    if stats["textures_not_found"]:
        print(f"[WARN] {len(stats['textures_not_found'])} textures not found in the asset set: "
              f"{stats['textures_not_found']}")
    if "generic_skin_swapped" in stats:
        print(f"[INFO] generic skin swapped for: {[c for c, _, _ in stats['generic_skin_swapped']]}")
        if stats["generic_skin_not_found"]:
            print(f"[WARN] generic skin texture not found for: {stats['generic_skin_not_found']}")
    for component, err in stats["failed_imports"]:
        print(f"[FAIL] import of component '{component}' failed: {err}")

    if sliders:
        morph = body_morphology.apply_body_morphology(bpy.data.objects[stats["armature"]], sliders)
        print(f"[INFO] morphs {sliders}: bones={list(morph['applied_bones'])} "
              f"reshaped_meshes={len(morph['reshaped_meshes'])}")
        if morph["skipped_bones"]:
            print(f"[WARN] bones missing from skeleton, skipped: {morph['skipped_bones']}")
        if morph["regional_bones"]:
            print(f"[INFO] bones not weighted on the mesh, scaled as a vertex region instead: "
                  f"{morph['regional_bones']}")
        if morph["no_effect_bones"]:
            print(f"[WARN] no mesh is weighted to these bones (or their children), their sliders "
                  f"changed nothing: {morph['no_effect_bones']}")

    armature = bpy.data.objects[stats["armature"]]
    deformations = []  # in the order they were baked
    glute_fit = None
    if args.bust_size:
        fit = body_morphology.fit_bust(armature, args.bust_size)
        if "error" in fit:
            print(f"[FAIL] bust fit {args.bust_size}: {fit['error']}")
            sys.exit(1)
        deformations.append(fit["deformation"])
        base, result = fit["base"], fit["result"]
        print(f"[BUST] base: underbust {base['underbust_cm']} cm, bust {base['bust_cm']} cm (~{base['size']})")
        if fit["band"]:
            band = fit["band"]
            print(f"[BUST] band {band['target_cm']}: ribcage widened x{band['factor']}")
            if not band["reached"]:
                print(f"[WARN] band {band['target_cm']} cm not reachable within ribcage factor "
                      f"{body_morphology.BAND_FIT_RANGE}: closest ribcage applied")
        print(f"[BUST] requested {args.bust_size}: target bust {fit['target_bust_cm']} cm, region scale "
              f"{fit['scale']} -> underbust {result['underbust_cm']} cm, bust {result['bust_cm']} cm "
              f"(~{result['size']})")
        if not fit["reached"]:
            print(f"[WARN] bust target {fit['target_bust_cm']} cm not reachable within region scale "
                  f"{body_morphology.BUST_FIT_RANGE}: closest shape applied")
        if fit["band_mismatch"]:
            print(f"[WARN] resulting underbust {result['underbust_cm']} cm is still off the band of "
                  f"{args.bust_size}")
    else:
        chest = body_morphology.measure_chest(armature)
        if chest:
            print(f"[BUST] underbust {chest['underbust_cm']} cm, bust {chest['bust_cm']} cm (~{chest['size']})")

    if args.glutes or args.glute_height:
        fit = glute_fit = body_morphology.fit_glutes(armature, args.glutes, args.glute_height)
        if "error" in fit:
            print(f"[FAIL] glute fit: {fit['error']}")
            sys.exit(1)
        deformations.append(fit["deformation"])
        print(f"[GLUTES] base: {_hips_line(fit['base'])}")
        print(f"[GLUTES] requested shape={args.glutes} height={args.glute_height} {fit['targets']}: "
              f"scales {fit['scales']}, apex shift {fit['shift_cm']} cm -> "
              + (_hips_line(fit["result"]) if fit["result"] else "result not measurable"))
        if not fit["reached"]:
            print(f"[WARN] glute targets {fit['targets']} not all reachable (scales "
                  f"{body_morphology.GLUTE_FIT_RANGE}, apex shift {body_morphology.GLUTE_SHIFT_RANGE} m): "
                  f"closest shape applied")
    else:
        hips = body_morphology.measure_hips(armature)
        if hips:
            print(f"[GLUTES] {_hips_line(hips)}")

    Path(args.output_blend).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=args.output_blend)
    print(f"[OK] Saved {args.output_blend}")
    if args.preview_png:
        Path(args.preview_png).parent.mkdir(parents=True, exist_ok=True)
        render_preview(stats["new_meshes"], args.preview_png)
        print(f"[OK] Rendered preview to {args.preview_png}")

    if args.deformation_out:
        # last: each variant rebuilds the scene (the .blend keeps the main pair)
        variants = fit_body_variants(args, build_kwargs, stats, glute_fit) if deformations else []
        out = Path(args.deformation_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "ped_root": os.path.abspath(args.ped_root), "ped_name": args.ped_name,
            "skeleton_yft": stats["skeleton_yft"] and os.path.abspath(stats["skeleton_yft"]),
            "components": {c: os.path.abspath(p) for c, p in stats["resolved_components"].items()},
            "deformations": [d.to_dict() for d in deformations],
            "variants": variants}, indent=1))
        print(f"[OK] Saved {out}")


if __name__ == "__main__":
    main()
