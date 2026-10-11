"""CPedVariationInfo (.ymt) XML generator — matches the REAL PSO struct.

Verified field-by-field against CodeWalker.Core/GameFiles/MetaTypes/MetaTypes.cs
(case MetaName.CPedVariationInfo / CPVComponentData / CPVDrawblData /
CPVTextureData / CPVDrawblData__CPVClothComponentData / CPedPropInfo) and the
XML-to-binary traversal rules in CodeWalker.Core/GameFiles/MetaTypes/XmlMeta.cs
(Traverse/TraverseArray/GetParsedArrayOfBytes), confirmed end-to-end by
actually converting the output through tools/CodeWalkerCli (see
docs/architecture-decisions.md point 11).

This FIXES the previous version of this module (see architecture-decisions.md
point 10), which had an invented/wrong schema: missing root booleans
(bHasTexVariations/bHasDrawblVariations/bHasLowLODs/bIsSuperLOD), wrong
nesting for aComponentData3/aDrawblData3, and fields that don't exist in the
real struct at all (pedXml_availTexs, aValidCompDrawbl, propMask at the wrong
level, a top-level <Name> element that isn't part of the binary struct).

CPedVariationInfo (root element <CPedVariationInfo>, 112 bytes):
  bHasTexVariations, bHasDrawblVariations, bHasLowLODs, bIsSuperLOD  (bool)
  availComp          ArrayOfBytes, 12 slots (head berd hair uppr lowr hand
                      feet teef accs task decl jbib; 255 = absent)
  aComponentData3    Array<CPVComponentData>, one per availComp slot
  aSelectionSets     Array<CPedSelectionSet>  -- left EMPTY here (see below)
  compInfos          Array<CComponentInfo>    -- left EMPTY here (see below)
  propInfo           CPedPropInfo { numAvailProps, aPropMetaData[], aAnchors[] }
  dlcName            Hash (omitted here -> stays 0 == base game, no DLC)

CPVComponentData (24 bytes): numAvailTex (byte), aDrawblData3: Array<CPVDrawblData>
CPVDrawblData (48 bytes): propMask, numAlternatives (byte), aTexData:
  Array<CPVTextureData>, clothData: CPVDrawblData__CPVClothComponentData
CPVTextureData (3 bytes): texId, distribution (byte)
CPVDrawblData__CPVClothComponentData (24 bytes): ownsCloth (bool)

KNOWN SIMPLIFICATION: `aSelectionSets` and `compInfos` are emitted as valid
but EMPTY arrays. `compInfos` (CComponentInfo: audio IDs, expression mod
floats stored as a raw hex byte dump, flags, inclusions/exclusions bitmasks,
the `ePedVarComp` ShortFlags enum for pedXml_vfxComps) carries gameplay
metadata (ambient audio variation, facial expression tweaks per component)
that this generator does not attempt to reproduce -- getting the enum/bitmask
encoding wrong would be worse than omitting it. The core visual component
swapping (which drawable/texture combinations exist per component slot) does
NOT depend on compInfos; it depends on availComp + aComponentData3, which
this module implements per the verified real struct. Treat compInfos as a
follow-up task if ambient ped audio/expression variation turns out to matter.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# availComp slot order (12 slots): head berd hair uppr lowr hand feet teef
# accs task decl jbib. 255 = component absent from this struct's aComponentData3.
_COMPONENT_ORDER = ["head", "berd", "hair", "uppr", "lowr", "hand", "feet",
                    "teef", "accs", "task", "decl", "jbib"]

DEFAULT_PRESENT_COMPONENTS = ("head", "uppr", "lowr", "hand", "feet")


def _bool_elem(parent: ET.Element, tag: str, value: bool) -> None:
    sub = ET.SubElement(parent, tag)
    sub.attrib = {"value": "true" if value else "false"}


def _byte_elem(parent: ET.Element, tag: str, value: int) -> None:
    sub = ET.SubElement(parent, tag)
    sub.attrib = {"value": str(value)}


def _build_cpv_drawbl_data(parent: ET.Element, num_textures: int = 1) -> None:
    """One CPVDrawblData item: a single drawable variant for a component,
    with `num_textures` texture alternatives (aTexData)."""
    item = ET.SubElement(parent, "Item")
    _byte_elem(item, "propMask", 0)
    _byte_elem(item, "numAlternatives", 0)
    tex_data = ET.SubElement(item, "aTexData")
    for tex_id in range(num_textures):
        tex_item = ET.SubElement(tex_data, "Item")
        _byte_elem(tex_item, "texId", tex_id)
        _byte_elem(tex_item, "distribution", 100 // max(num_textures, 1))
    cloth_data = ET.SubElement(item, "clothData")
    _bool_elem(cloth_data, "ownsCloth", False)


def _build_cpv_component_data(parent: ET.Element, num_drawables: int = 1,
                               num_textures: int = 1) -> None:
    """One CPVComponentData item: `numAvailTex` texture count plus
    `num_drawables` CPVDrawblData entries (one per outfit variant)."""
    item = ET.SubElement(parent, "Item")
    _byte_elem(item, "numAvailTex", num_textures)
    drawbl_data = ET.SubElement(item, "aDrawblData3")
    for _ in range(num_drawables):
        _build_cpv_drawbl_data(drawbl_data, num_textures)


def build_ymt_xml(model_name: str, present_components: tuple[str, ...] = DEFAULT_PRESENT_COMPONENTS,
                   num_drawables_per_component: int = 1, num_textures_per_drawable: int = 1) -> ET.ElementTree:
    root = ET.Element("CPedVariationInfo")

    _bool_elem(root, "bHasTexVariations", num_textures_per_drawable > 1)
    _bool_elem(root, "bHasDrawblVariations", num_drawables_per_component > 1)
    _bool_elem(root, "bHasLowLODs", False)
    _bool_elem(root, "bIsSuperLOD", False)

    avail_comp = ET.SubElement(root, "availComp")
    avail_comp.text = " ".join("0" if comp in present_components else "255" for comp in _COMPONENT_ORDER)

    component_data3 = ET.SubElement(root, "aComponentData3")
    for comp in _COMPONENT_ORDER:
        if comp not in present_components:
            continue
        _build_cpv_component_data(component_data3, num_drawables_per_component, num_textures_per_drawable)

    # Left empty: see module docstring "KNOWN SIMPLIFICATION".
    ET.SubElement(root, "aSelectionSets")
    ET.SubElement(root, "compInfos")

    prop_info = ET.SubElement(root, "propInfo")
    _byte_elem(prop_info, "numAvailProps", 0)
    # aPropMetaData / aAnchors omitted -> stay empty (GetEntryNode returns
    # null for a missing child, Traverse skips it, array pointer stays 0).

    # dlcName omitted entirely -> hash stays 0 (base game, no DLC).

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    return tree


def write_ymt_xml(output_path: Path, model_name: str,
                   present_components: tuple[str, ...] = DEFAULT_PRESENT_COMPONENTS,
                   num_drawables_per_component: int = 1, num_textures_per_drawable: int = 1) -> Path:
    tree = build_ymt_xml(model_name, present_components, num_drawables_per_component, num_textures_per_drawable)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(output_path)
    return output_path
