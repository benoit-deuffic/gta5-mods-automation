"""Prepare the user's raw extracted ped assets for the Blender build step.

    python -m scripts.industrialize_pipeline

1. Every directory under ``import/`` holding raw RAGE binaries (.ydd/.yft/
   .ytd/.ymt) is converted to CodeWalker-XML into ``import/<name>_xml/``
   (mirrored tree, see codewalker_convert.export_tree_to_xml). A conversion
   is redone only when a source file is newer than the last successful one
   (``.converted`` stamp), so editing/adding a raw file is picked up while an
   unchanged tree is not re-converted.
2. ``import_xml/`` is rebuilt: one symlink per converted directory
   (``import_xml/<name> -> import/<name>_xml``) -- the ``--ped-root`` that
   scripts/import_layered_ped.py reads.
3. Each ped skeleton ``import/<ped>.yft`` (standard GTA layout: next to the
   ``<ped>/`` component folder, not inside it) becomes
   ``import_xml/<ped>.yft.xml``, which import_layered_ped.py hands to Sollumz
   so the components' native skinning is kept.

Raises IndustrializeError (exit code 1 from the CLI) on any failed
conversion -- a partially converted tree must not silently feed the build.
"""
from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

from scripts.codewalker_convert import (
    EXPORT_XML_EXTENSIONS, _resolve_cli_path, export_binary_to_xml, export_tree_to_xml,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMPORT_DIR = PROJECT_ROOT / "import"
IMPORT_XML_DIR = PROJECT_ROOT / "import_xml"
STAMP_NAME = ".converted"


class IndustrializeError(RuntimeError):
    pass


def _raw_files(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EXPORT_XML_EXTENSIONS]


def _is_stale(sources: list[Path], xml_out: Path) -> bool:
    stamp = xml_out / STAMP_NAME
    if not stamp.is_file():
        return True
    return max(p.stat().st_mtime for p in sources) > stamp.stat().st_mtime


def industrialize(import_dir: Path = IMPORT_DIR, import_xml_dir: Path = IMPORT_XML_DIR) -> dict:
    """Run steps 1-3 (module docstring). Returns {"converted": [...],
    "up_to_date": [...], "skeletons": [...]} (directory / file names)."""
    if _resolve_cli_path() is None:
        raise IndustrializeError("CodeWalker CLI not found (CODEWALKER_CLI_PATH, PATH, or "
                                 "tools/CodeWalkerCli/publish/codewalker-cli)")

    summary = {"converted": [], "up_to_date": [], "skeletons": []}
    asset_dirs = []
    for folder in sorted(p for p in import_dir.iterdir() if p.is_dir() and not p.name.endswith("_xml")):
        sources = _raw_files(folder)
        if not sources:
            continue
        asset_dirs.append(folder)
        xml_out = import_dir / f"{folder.name}_xml"
        if not _is_stale(sources, xml_out):
            summary["up_to_date"].append(folder.name)
            continue
        logger.info("converting %s (%d raw files)", folder.name, len(sources))
        if xml_out.exists():
            shutil.rmtree(xml_out)  # drop outputs of since-deleted sources too
        result = export_tree_to_xml(folder, xml_out)
        if result.cli_missing or result.failed:
            raise IndustrializeError(f"conversion of {folder.name} failed: "
                                     f"{'CLI missing' if result.cli_missing else result.failed[:3]}")
        (xml_out / STAMP_NAME).touch()
        summary["converted"].append(folder.name)

    if import_xml_dir.exists():
        shutil.rmtree(import_xml_dir)
    import_xml_dir.mkdir(parents=True)
    for folder in asset_dirs:
        (import_xml_dir / folder.name).symlink_to(import_dir / f"{folder.name}_xml", target_is_directory=True)

    for yft in sorted(import_dir.glob("*.yft")):
        result = export_binary_to_xml(yft, import_xml_dir)
        if result.status != "converted":
            raise IndustrializeError(f"skeleton {yft.name} not converted ({result.status}): {result.message}")
        summary["skeletons"].append(yft.name)
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        summary = industrialize()
    except IndustrializeError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("converted=%s up_to_date=%s skeletons=%s",
                summary["converted"], summary["up_to_date"], summary["skeletons"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
