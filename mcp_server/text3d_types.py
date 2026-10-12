"""Shared types for the pluggable text-to-3D backends (mcp_server/text3d_backend.py).

Split out of the old meshy_client.py (removed 2026-10-12, see
docs/architecture-decisions.md point 17) so mcp_server/tripo_client.py and
mcp_server/text3d_backend.py don't need to import from each other's cloud
client modules just to share a result dataclass and the local test fixtures
directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@dataclass
class GeneratedAsset:
    fbx_path: Path
    texture_paths: list[Path]
    mock: bool
    task_id: str | None = None


class Text3DBackendError(RuntimeError):
    """Raised by any Text3DBackend implementation for generation failures."""
    pass
