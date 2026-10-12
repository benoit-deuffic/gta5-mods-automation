import asyncio
from pathlib import Path

import pytest

from mcp_server import text3d_backend


def test_get_backend_default_is_tripo():
    backend = text3d_backend.get_backend()
    assert isinstance(backend, text3d_backend.TripoBackend)


def test_get_backend_tripo_explicit(monkeypatch):
    monkeypatch.setenv("TEXT3D_BACKEND", "tripo")
    backend = text3d_backend.get_backend()
    assert isinstance(backend, text3d_backend.TripoBackend)


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
    # LocalText3DBackend is self-hosted and has no cloud API key of its own;
    # it mock-falls-back purely because is_available() is False on this
    # sandbox (no GPU script). TRIPO_API_KEY is irrelevant to it, but we
    # clear it anyway to make sure this test doesn't accidentally depend on
    # whatever is in the ambient environment.
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    monkeypatch.setenv("PIPELINE_MOCK", "auto")
    backend = text3d_backend.LocalText3DBackend()
    asset = asyncio.run(backend.generate_humanoid("a prompt", "LocalTest01", tmp_path))
    assert asset.mock is True
    assert asset.fbx_path.exists()
    assert len(asset.texture_paths) == 1
