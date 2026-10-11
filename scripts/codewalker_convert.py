"""Wrapper around a CodeWalker.Core-based CLI that converts CW-XML to binary
RAGE formats (.ydd, .yft, .ytd, .ymt).

CodeWalker.Core (netstandard2.0) is not vendored here: building a Linux CLI
from it is unverified by this pipeline (see skill reference
`toolchain-facts.md`). This module defines the stable interface the rest of
the pipeline depends on, and runs the real CLI if `CODEWALKER_CLI_PATH` points
to a working executable (expected contract: `<cli> convert --in <xml_dir> --out <bin_dir>`,
exit code 0 on success). If the CLI isn't available, conversion is reported as
"pending-real-tool" — it never fabricates a fake binary file claiming to be a
real RAGE archive.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConversionResult:
    status: str  # "converted" | "mock-skipped" | "error"
    produced_files: list[str] = field(default_factory=list)
    message: str = ""


def _resolve_cli_path() -> str | None:
    configured = os.environ.get("CODEWALKER_CLI_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("codewalker-cli")
    return found


def convert_xml_to_binary(xml_dir: Path, output_dir: Path, model_name: str,
                           extensions: tuple[str, ...] = (".ydd", ".yft", ".ytd", ".ymt")) -> ConversionResult:
    cli_path = _resolve_cli_path()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not cli_path:
        return ConversionResult(
            status="mock-skipped",
            message=(
                "CodeWalker.Core CLI not configured/found (CODEWALKER_CLI_PATH). "
                "XML->binary conversion was NOT performed; only CW-XML intermediate "
                "files exist. Install/build the CLI and set CODEWALKER_CLI_PATH to "
                "produce real .ydd/.yft/.ytd/.ymt binaries."
            ),
        )

    cmd = [cli_path, "convert", "--in", str(xml_dir), "--out", str(output_dir), "--name", model_name]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ConversionResult(status="error", message=f"CLI invocation failed: {exc}")

    if proc.returncode != 0:
        return ConversionResult(
            status="error",
            message=f"CLI exited {proc.returncode}: {proc.stderr[-2000:]}",
        )

    produced = [str(p) for p in output_dir.glob(f"{model_name}.*") if p.suffix in extensions]
    return ConversionResult(status="converted", produced_files=produced, message=proc.stdout[-2000:])
