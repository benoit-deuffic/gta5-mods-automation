import asyncio
from pathlib import Path

import pytest

from mcp_server import text3d_backend


def test_get_backend_default_is_meshy():
    backend = text3d_backend.get_backend()
    assert isinstance(backend, text3d_backend.MeshyBackend)


def test_get_backend_local(monkeypatch):
    monkeypatch.setenv("TEXT3D_BACKEND", "local")
    backend = text3d_backend.get_backend()
    assert isinstance(backend, text3d_backend.LocalText3DBackend)


def test_get_backend_invalid_raises():
    with pytest.raises(ValueError):
        text3d_backend.get_backend("bogus")


def test_local_backend_not_available_on_this_sandbox():
    backend = text3d_backend.LocalText3DBackend()
    assert backend.is_available() is False  # no GPU script here, by design


def test_local_backend_mock_fallback_produces_fixture_files(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MESHY_API_KEY", raising=False)
    monkeypatch.setenv("PIPELINE_MOCK", "auto")
    backend = text3d_backend.LocalText3DBackend()
    asset = asyncio.run(backend.generate_humanoid("a prompt", "LocalTest01", tmp_path))
    assert asset.mock is True
    assert asset.fbx_path.exists()
    assert len(asset.texture_paths) == 1
