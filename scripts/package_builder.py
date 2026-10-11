"""Builds the manual-install delivery package (zip, optionally .oiv) for a
compiled ped, per decision: delivery = AddonPeds/PedSelector + standalone
dlcpack, transported as a zip the user installs by hand on their Windows PC
(docs/architecture-decisions.md points 2-3).

Produces two subfolders inside the zip, mirroring formats-and-delivery.md:

  addonpeds_pedselector/
    UserMods/ap_m.xml                              (merged/updated manifest)
    dlcpacks/pedselector/dlc.rpf/peds.rpf/<name>.*  (placeholder layout: real
      RPF packing requires CodeWalker.Core; until available we stage the raw
      files in the same relative path so a manual RPF repack is a straight
      folder copy)
  dlcpack_standalone/<model_name>/
    content.xml, setup2.xml, common/data/peds.meta,
    x64/models/cdimages/<model_name>/<artifacts>

Nothing here invents binary RAGE content: whatever extension-correct files
compile_gtav_ped + codewalker_convert produced are copied as-is, mock or real.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

CONTENT_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<CDataFileMgr__ContentsOfDataFileXml>
  <dataFiles>
    <Item>
      <filename>dlc_{name}:/common/data/peds.meta</filename>
      <fileType>PED_METADATA_FILE</fileType>
      <overlay value="false" />
      <disabled value="false" />
      <persistent value="false" />
    </Item>
    <Item>
      <filename>dlc_{name}:/x64/models/cdimages/{name}.rpf</filename>
      <fileType>RPF_FILE</fileType>
      <overlay value="false" />
      <disabled value="false" />
      <persistent value="false" />
    </Item>
  </dataFiles>
  <contentChangeSets>
    <Item>
      <changeSetName>{name_upper}_AUTOGEN</changeSetName>
      <filesToEnable>
        <Item>dlc_{name}:/common/data/peds.meta</Item>
        <Item>dlc_{name}:/x64/models/cdimages/{name}.rpf</Item>
      </filesToEnable>
    </Item>
  </contentChangeSets>
</CDataFileMgr__ContentsOfDataFileXml>
"""

SETUP2_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<SSetupData>
  <deviceName>dlc_{name}</deviceName>
  <datFile>content.xml</datFile>
  <timeStamp>00/00/0000 00:00:00</timeStamp>
  <nameHash>{name}</nameHash>
  <contentChangeSets />
  <contentChangeSetGroups>
    <Item>
      <NameHash>GROUP_STARTUP</NameHash>
      <ContentChangeSets>
        <Item>{name_upper}_AUTOGEN</Item>
      </ContentChangeSets>
    </Item>
  </contentChangeSetGroups>
  <startupScript />
  <scriptCallstackSize value="0" />
  <type>EXTRACONTENT_COMPAT_PACK</type>
  <order value="127" />
</SSetupData>
"""


def _collect_artifacts(compiled_dir: Path, model_name: str) -> list[Path]:
    return sorted(p for p in compiled_dir.glob(f"{model_name}.*") if p.is_file())


def build_addonpeds_layout(staging_root: Path, model_name: str, compiled_dir: Path,
                           manifest_path: Path) -> Path:
    base = staging_root / "addonpeds_pedselector"
    usermods = base / "UserMods"
    usermods.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        shutil.copy2(manifest_path, usermods / "ap_m.xml")

    rpf_stage = base / "dlcpacks" / "pedselector" / "dlc.rpf" / "peds.rpf"
    rpf_stage.mkdir(parents=True, exist_ok=True)
    for artifact in _collect_artifacts(compiled_dir, model_name):
        shutil.copy2(artifact, rpf_stage / artifact.name)

    readme = base / "README_INSTALL.txt"
    readme.write_text(
        "Installation manuelle (AddonPeds/PedSelector) :\n"
        "1. Copier UserMods/ap_m.xml dans Documents/Rockstar Games/GTA V/UserMods/ap_m.xml\n"
        "   (fusionner si le fichier existe deja, ne pas ecraser les autres <Item>).\n"
        "2. Les fichiers sous dlcpacks/pedselector/dlc.rpf/peds.rpf/ doivent etre empaquetes\n"
        "   dans le peds.rpf reel de ton dlcpack AddonPeds/PedSelector installe\n"
        "   (mods/update/x64/dlcpacks/pedselector/dlc.rpf/peds.rpf) via OpenIV, en remplacant\n"
        "   ce dossier par un vrai RPF. Ce pipeline ne packe pas de RPF binaire sans\n"
        "   CodeWalker.Core (voir scripts/codewalker_convert.py).\n",
        encoding="utf-8",
    )
    return base


def build_dlcpack_layout(staging_root: Path, model_name: str, compiled_dir: Path) -> Path:
    base = staging_root / "dlcpack_standalone" / model_name
    (base / "common" / "data").mkdir(parents=True, exist_ok=True)
    cdimages = base / "x64" / "models" / "cdimages" / model_name
    cdimages.mkdir(parents=True, exist_ok=True)

    for artifact in _collect_artifacts(compiled_dir, model_name):
        shutil.copy2(artifact, cdimages / artifact.name)

    (base / "content.xml").write_text(
        CONTENT_XML_TEMPLATE.format(name=model_name, name_upper=model_name.upper()), encoding="utf-8")
    (base / "setup2.xml").write_text(
        SETUP2_XML_TEMPLATE.format(name=model_name, name_upper=model_name.upper()), encoding="utf-8")
    (base / "common" / "data" / "peds.meta").write_text(
        "<!-- TODO: generate from scripts/ymt_builder.py CPedModelInfo data, "
        "same field set as ap_m.xml Item -->\n", encoding="utf-8")

    readme = base / "README_INSTALL.txt"
    readme.write_text(
        f"Installation manuelle (dlcpack autonome) :\n"
        f"1. Copier le dossier '{model_name}/' dans dlcpacks/ de ton installation GTA V.\n"
        f"2. Ajouter 'dlcpacks:/{model_name}/' a update/update.rpf/common/data/dlclist.xml.\n"
        f"3. x64/models/cdimages/{model_name}/ doit etre reempaquete en .rpf (OpenIV ou "
        f"CodeWalker) avant d'etre un dlcpack fonctionnel.\n",
        encoding="utf-8",
    )
    return base


def build_delivery_zip(output_dir: Path, model_name: str, compiled_dir: Path,
                        manifest_path: Path, staging_root: Path | None = None) -> Path:
    staging_root = staging_root or (output_dir / f"_staging_{model_name}")
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)

    build_addonpeds_layout(staging_root, model_name, compiled_dir, manifest_path)
    build_dlcpack_layout(staging_root, model_name, compiled_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{model_name}_delivery.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in staging_root.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(staging_root))

    shutil.rmtree(staging_root)
    return zip_path
