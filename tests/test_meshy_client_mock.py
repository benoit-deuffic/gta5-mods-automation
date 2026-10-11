import asyncio
from pathlib import Path

import pytest

from mcp_server import meshy_client


def test_is_mock_mode_true_without_key(monkeypatch):
    monkeypatch.delenv("MESHY_API_KEY", raising=False)
    monkeypatch.setenv("PIPELINE_MOCK", "auto")
    assert meshy_client.is_mock_mode() is True


def test_is_mock_mode_false_with_key(monkeypatch):
    monkeypatch.setenv("MESHY_API_KEY", "msy_fake")
    monkeypatch.setenv("PIPELINE_MOCK", "auto")
    assert meshy_client.is_mock_mode() is False


def test_is_mock_mode_forced(monkeypatch):
    monkeypatch.setenv("MESHY_API_KEY", "msy_fake")
    monkeypatch.setenv("PIPELINE_MOCK", "1")
    assert meshy_client.is_mock_mode() is True


def test_generate_humanoid_mock_mode(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MESHY_API_KEY", raising=False)
    monkeypatch.setenv("PIPELINE_MOCK", "auto")

    client = meshy_client.MeshyClient()
    asset = asyncio.run(client.generate_humanoid("a beach surfer", "BeachSurfer01", tmp_path))

    assert asset.mock is True
    assert asset.fbx_path.exists()
    assert asset.fbx_path.stat().st_size > 0
    assert len(asset.texture_paths) == 1
    assert asset.texture_paths[0].exists()
