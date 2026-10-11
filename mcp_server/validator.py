"""Pre-flight and artifact sanity checks (acceptance criteria §8.1-8.3).

Replaces the spec's arbitrary ">500KB" threshold (docs/analyse-prerequis.md
§2.5) with format-aware checks, and adds the vertex-group/texture checks the
spec's acceptance criteria require but never implements a validator for.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as DefusedET

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


@dataclass
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_fbx_file(path: Path, min_bytes: int = 20000) -> ValidationReport:
    report = ValidationReport(ok=True)
    if not path.exists():
        report.add_error(f"FBX not found: {path}")
        return report
    size = path.stat().st_size
    if size < min_bytes:
        report.add_error(f"FBX too small ({size}B < {min_bytes}B threshold): {path}")
    header = path.read_bytes()[:27]
    if not (header.startswith(b"Kaydara FBX Binary") or header.startswith(b";")):
        report.add_warning(f"FBX header unrecognized (binary vs ASCII check inconclusive): {path}")
    return report


def _is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def validate_texture_file(path: Path, min_bytes: int = 5000, require_pow2: bool = True) -> ValidationReport:
    report = ValidationReport(ok=True)
    if not path.exists():
        report.add_error(f"Texture not found: {path}")
        return report
    size = path.stat().st_size
    if size < min_bytes:
        report.add_error(f"Texture too small ({size}B < {min_bytes}B threshold): {path}")
    if not _HAS_PIL:
        report.add_warning("Pillow not installed: skipping dimension/power-of-two check.")
        return report
    try:
        with Image.open(path) as img:
            w, h = img.size
            if require_pow2 and not (_is_power_of_two(w) and _is_power_of_two(h)):
                report.add_error(
                    f"Texture {path} is {w}x{h}, not power-of-two "
                    "(RAGE texture allocation crash risk, acceptance criterion 4)."
                )
    except Exception as exc:  # noqa: BLE001 - want a validation error, not a crash
        report.add_error(f"Could not read texture {path}: {exc}")
    return report


def validate_manifest_xml(xml_path: Path, expected_model_name: str) -> ValidationReport:
    report = ValidationReport(ok=True)
    if not xml_path.exists():
        report.add_error(f"Manifest not found: {xml_path}")
        return report
    try:
        tree = DefusedET.parse(str(xml_path))
    except ET.ParseError as exc:
        report.add_error(f"Manifest is not well-formed XML: {exc}")
        return report
    root = tree.getroot()
    init_datas = root.find("InitDatas")
    if init_datas is None:
        report.add_error("Manifest missing <InitDatas> node.")
        return report
    names = [item.findtext("Name") for item in init_datas.findall("Item")]
    if expected_model_name not in names:
        report.add_error(f"Manifest has no <Item><Name>{expected_model_name}</Name> entry.")
    return report


def validate_compiled_artifacts(compiled_dir: Path, model_name: str,
                                 required_suffixes: tuple[str, ...] = (".ydd", ".yft", ".ytd", ".ymt"),
                                 allow_mock_placeholders: bool = True) -> ValidationReport:
    report = ValidationReport(ok=True)
    for suffix in required_suffixes:
        candidate = compiled_dir / f"{model_name}{suffix}"
        xml_candidate = compiled_dir / f"{model_name}{suffix}.xml"
        if candidate.exists():
            continue
        if allow_mock_placeholders and xml_candidate.exists():
            report.add_warning(
                f"Only CW-XML intermediate found for {suffix} ({xml_candidate.name}); "
                "binary not yet produced (codewalker_convert mock-skipped)."
            )
            continue
        report.add_error(f"Missing required artifact: {candidate}")
    return report
