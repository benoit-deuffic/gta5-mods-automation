"""Minimal CPedVariationInfo (.ymt) XML generator.

The original spec lists `.ymt` as a delivery artifact but never produces it
(see docs/analyse-prerequis.md §2.4). This module generates a *minimal but
structurally valid* CPedVariationInfo XML skeleton for a single-outfit ped
(no alternate variations), to be converted to binary .ymt by CodeWalker.Core
(scripts/codewalker_convert.py).

Component order (availComp, 12 slots): head, berd, hair, uppr, lowr, hand,
feet, teef, accs, task, decl, jbib. 255 = component absent.
This generator assumes the generated mesh replaces head+uppr+lowr+hand+feet
(a full-body humanoid) and leaves the rest absent, matching the Blender
export step (scripts/blender_rig_export.py) which produces a single
drawable dictionary for the whole body.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# order: head berd hair uppr lowr hand feet teef accs task decl jbib
_COMPONENT_ORDER = ["head", "berd", "hair", "uppr", "lowr", "hand", "feet",
                    "teef", "accs", "task", "decl", "jbib"]
_VFX_COMP_NAME = {
    "head": "PV_COMP_HEAD", "berd": "PV_COMP_BERD", "hair": "PV_COMP_HAIR",
    "uppr": "PV_COMP_UPPR", "lowr": "PV_COMP_LOWR", "hand": "PV_COMP_HAND",
    "feet": "PV_COMP_FEET", "teef": "PV_COMP_TEEF", "accs": "PV_COMP_ACCS",
    "task": "PV_COMP_TASK", "decl": "PV_COMP_DECL", "jbib": "PV_COMP_JBIB",
}

DEFAULT_PRESENT_COMPONENTS = ("head", "uppr", "lowr", "hand", "feet")


def build_ymt_xml(model_name: str, present_components: tuple[str, ...] = DEFAULT_PRESENT_COMPONENTS) -> ET.ElementTree:
    root = ET.Element("CPedVariationInfo")
    ET.SubElement(root, "Name").text = model_name

    avail_comp = ET.SubElement(root, "availComp", {"content": "char_array"})
    values = []
    for comp in _COMPONENT_ORDER:
        values.append("0" if comp in present_components else "255")
    avail_comp.text = " ".join(values)

    avail_props = ET.SubElement(root, "availProps", {"content": "char_array"})
    avail_props.text = " ".join(["255"] * 12)

    avail_texs = ET.SubElement(root, "pedXml_availTexs", {"content": "char_array"})
    avail_texs.text = " ".join(["0" if c in present_components else "255" for c in _COMPONENT_ORDER])

    valid_comp_drawbl = ET.SubElement(root, "aValidCompDrawbl")
    for comp in _COMPONENT_ORDER:
        item = ET.SubElement(valid_comp_drawbl, "Item")
        if comp in present_components:
            drawbl_item = ET.SubElement(item, "Item")
            drawbl_item.attrib = {"value": "0"}

    drawbl_data = ET.SubElement(root, "aDrawblData3")
    for comp in present_components:
        comp_item = ET.SubElement(drawbl_data, "Item")
        ET.SubElement(comp_item, "numAvailTex").attrib = {"value": "1"}
        tex_list = ET.SubElement(comp_item, "aTexData")
        tex_item = ET.SubElement(tex_list, "Item")
        ET.SubElement(tex_item, "texId").attrib = {"value": "0"}
        ET.SubElement(tex_item, "distribution").attrib = {"value": "100"}
        ET.SubElement(comp_item, "propMask").attrib = {"value": "0"}
        ET.SubElement(comp_item, "clothData")
        owns_cloth = ET.SubElement(comp_item, "ownsCloth")
        owns_cloth.attrib = {"value": "false"}

    comp_infos = ET.SubElement(root, "compInfos")
    for idx, comp in enumerate(present_components):
        info = ET.SubElement(comp_infos, "Item")
        ET.SubElement(info, "pedXml_vfxComps").text = _VFX_COMP_NAME[comp]
        ET.SubElement(info, "pedXml_compIdx").attrib = {"value": str(idx)}
        ET.SubElement(info, "pedXml_drawblIdx").attrib = {"value": "0"}

    prop_info = ET.SubElement(root, "propInfo")
    ET.SubElement(prop_info, "numAvailProps").attrib = {"value": "0"}

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    return tree


def write_ymt_xml(output_path: Path, model_name: str,
                   present_components: tuple[str, ...] = DEFAULT_PRESENT_COMPONENTS) -> Path:
    tree = build_ymt_xml(model_name, present_components)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(output_path)
    return output_path
