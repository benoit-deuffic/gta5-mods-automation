"""Atomic, safe manipulation of UserMods/ap_m.xml (CPedModelInfo__InitDataList).

Corrections vs the original spec (see docs/analyse-prerequis.md §2.3):
- Uses defusedxml for parsing (untrusted/compat XML) instead of raw xml.etree.
- IsStreamedGfx is derived from the actual delivery layout, not hard-coded "false"
  while a different default sneaks in elsewhere.
- Gender-specific fields (clipsets, pose matcher, capsule, expression set...) are
  derived from the `gender` argument instead of being hard-coded to female values
  regardless of ped gender (spec bug: PoseMatcherName="Male" under a "female" template).
- Field order matches CodeWalker's CPedModelInfo__InitData (GameFiles/FileTypes/PedsFile.cs).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree as DefusedET
import xml.etree.ElementTree as ET

ROOT_TAG = "CPedModelInfo__InitDataList"
LIST_TAG = "InitDatas"
ITEM_TAG = "Item"

_GENDER_DEFAULTS = {
    "female": dict(
        relationship_group="CIVFEMALE",
        clipset="move_f@generic",
        expr_set="expr_set_ambient_female",
        gesture_group="ANIM_GROUP_GESTURE_F_GENERIC",
        facial_clipset_group="facial_clipset_group_gen_female",
        viseme_clipset="ANIM_GROUP_VISEMES_F_LO",
        pose_matcher="Female",
        pose_matcher_prone="Female_prone",
        capsule="STANDARD_FEMALE",
        radio1="RADIO_GENRE_PUNK",
        radio2="RADIO_GENRE_JAZZ",
    ),
    "male": dict(
        relationship_group="CIVMALE",
        clipset="move_m@generic",
        expr_set="expr_set_ambient_male",
        gesture_group="ANIM_GROUP_GESTURE_M_GENERIC",
        facial_clipset_group="facial_clipset_group_gen_male",
        viseme_clipset="ANIM_GROUP_VISEMES_M_LO",
        pose_matcher="Male",
        pose_matcher_prone="Male_prone",
        capsule="STANDARD_MALE",
        radio1="RADIO_GENRE_ROCK",
        radio2="RADIO_GENRE_HIPHOP",
    ),
}


class ManifestError(ValueError):
    pass


def _bool_elem(parent: ET.Element, tag: str, value: bool) -> None:
    sub = ET.SubElement(parent, tag)
    sub.attrib = {"value": "true" if value else "false"}


def build_item_fields(model_name: str, gender: str, is_streamed_gfx: bool,
                       ped_type: str | None = None) -> list[tuple[str, object]]:
    if gender not in _GENDER_DEFAULTS:
        raise ManifestError(f"gender must be one of {list(_GENDER_DEFAULTS)}, got {gender!r}")
    g = _GENDER_DEFAULTS[gender]
    pedtype = ped_type or g["relationship_group"]
    return [
        ("Name", model_name),
        ("PropsName", "null"),
        ("ClipDictionaryName", g["clipset"]),
        ("BlendShapeFileName", "null"),
        ("ExpressionSetName", g["expr_set"]),
        ("ExpressionDictionaryName", "null"),
        ("ExpressionName", "null"),
        ("Pedtype", pedtype),
        ("MovementClipSet", g["clipset"]),
        ("StrafeClipSet", "move_ped_strafing"),
        ("MovementToStrafeClipSet", "move_ped_to_strafe"),
        ("InjuredStrafeClipSet", "move_strafe_injured"),
        ("FullBodyDamageClipSet", "dam_ko"),
        ("AdditiveDamageClipSet", "dam_ad"),
        ("DefaultGestureClipSet", g["gesture_group"]),
        ("FacialClipsetGroupName", g["facial_clipset_group"]),
        ("DefaultVisemeClipSet", g["viseme_clipset"]),
        ("SidestepClipSet", "CLIP_SET_ID_INVALID"),
        ("PoseMatcherName", g["pose_matcher"]),
        ("PoseMatcherProneName", g["pose_matcher_prone"]),
        ("GetupSetHash", "NMBS_SLOW_GETUPS"),
        ("CreatureMetadataName", "null"),
        ("DecisionMakerName", "DEFAULT"),
        ("MotionTaskDataSetName", "STANDARD_PED"),
        ("DefaultTaskDataSetName", "STANDARD_PED"),
        ("PedCapsuleName", g["capsule"]),
        ("PedLayoutName", ""),
        ("PedComponentSetName", ""),
        ("PedComponentClothName", ""),
        ("PedIKSettingsName", ""),
        ("TaskDataName", ""),
        ("__BOOL__IsStreamedGfx", is_streamed_gfx),
        ("__BOOL__AmbulanceShouldRespondTo", True),
        ("__BOOL__CanRideBikeWithNoHelmet", False),
        ("__BOOL__CanSpawnInCar", True),
        ("__BOOL__IsHeadBlendPed", False),
        ("__BOOL__bOnlyBulkyItemVariations", False),
        ("RelationshipGroup", g["relationship_group"]),
        ("NavCapabilitiesName", "STANDARD_PED"),
        ("PerceptionInfo", "DEFAULT_PERCEPTION"),
        ("DefaultBrawlingStyle", "BS_AI"),
        ("DefaultUnarmedWeapon", "WEAPON_UNARMED"),
        ("Personality", "DEFAULT"),
        ("CombatInfo", "DEFAULT"),
        ("VfxInfoName", "VFXPEDINFO_HUMAN_GENERIC"),
        ("AmbientClipsForFlee", "FLEE"),
        ("Radio1", g["radio1"]),
        ("Radio2", g["radio2"]),
        ("PedVoiceGroup", "BAR_PERSON_PVG"),
        ("AbilityType", "SAT_NONE"),
        ("ThermalBehaviour", "TB_WARM"),
        ("SuperlodType", "SLOD_HUMAN"),
        ("ScenarioPopStreamingSlot", "SCENARIO_POP_STREAMING_NORMAL"),
        ("DefaultSpawningPreference", "DSP_NORMAL"),
        ("__FLOAT__DefaultRemoveRangeMultiplier", "1.000000"),
        ("__BOOL__AllowCloseSpawning", False),
    ]


def _new_manifest_tree() -> ET.ElementTree:
    root = ET.Element(ROOT_TAG)
    ET.SubElement(root, LIST_TAG)
    return ET.ElementTree(root)


def load_or_create_manifest(xml_path: Path) -> ET.ElementTree:
    if xml_path.exists():
        parsed = DefusedET.parse(str(xml_path))
        root = parsed.getroot()
        if root.tag != ROOT_TAG:
            raise ManifestError(f"Unexpected root tag {root.tag!r} in {xml_path}")
        # defusedxml returns a cElementTree-compatible tree; rewrap for ET.indent/write support.
        return ET.ElementTree(ET.fromstring(DefusedET.tostring(root)))
    return _new_manifest_tree()


def add_ped_to_manifest(xml_path: Path, model_name: str, gender: str = "female",
                         is_streamed_gfx: bool = False, ped_type: str | None = None) -> bool:
    """Idempotently add/update a ped Item. Returns True if a change was written."""
    tree = load_or_create_manifest(xml_path)
    root = tree.getroot()
    init_datas = root.find(LIST_TAG)
    if init_datas is None:
        init_datas = ET.SubElement(root, LIST_TAG)

    for item in init_datas.findall(ITEM_TAG):
        name_node = item.find("Name")
        if name_node is not None and name_node.text == model_name:
            init_datas.remove(item)  # re-add fresh (update-in-place semantics)
            break

    new_item = ET.SubElement(init_datas, ITEM_TAG)
    for tag, val in build_item_fields(model_name, gender, is_streamed_gfx, ped_type):
        if tag.startswith("__BOOL__"):
            _bool_elem(new_item, tag[len("__BOOL__"):], bool(val))
        elif tag.startswith("__FLOAT__"):
            sub = ET.SubElement(new_item, tag[len("__FLOAT__"):])
            sub.attrib = {"value": str(val)}
        else:
            sub = ET.SubElement(new_item, tag)
            if val:
                sub.text = str(val)

    ET.indent(tree, space="  ", level=0)
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = xml_path.with_suffix(".tmp")
    tree.write(temp_path, encoding="utf-8", xml_declaration=True)
    temp_path.replace(xml_path)
    return True


def list_registered_peds(xml_path: Path) -> list[str]:
    if not xml_path.exists():
        return []
    tree = DefusedET.parse(str(xml_path))
    init_datas = tree.getroot().find(LIST_TAG)
    if init_datas is None:
        return []
    names = []
    for item in init_datas.findall(ITEM_TAG):
        name_node = item.find("Name")
        if name_node is not None and name_node.text:
            names.append(name_node.text)
    return names
