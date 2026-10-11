import asyncio
from pathlib import Path

import httpx
import pytest

from mcp_server import tripo_client


def test_is_tripo_mock_mode_true_without_key(monkeypatch):
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    monkeypatch.setenv("PIPELINE_MOCK", "auto")
    assert tripo_client.is_tripo_mock_mode() is True


def test_is_tripo_mock_mode_false_with_key(monkeypatch):
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_fake")
    monkeypatch.setenv("PIPELINE_MOCK", "auto")
    assert tripo_client.is_tripo_mock_mode() is False


def test_generate_humanoid_mock_mode(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    monkeypatch.setenv("PIPELINE_MOCK", "auto")

    client = tripo_client.TripoClient()
    asset = asyncio.run(client.generate_humanoid("a beach surfer", "BeachSurfer01", tmp_path))

    assert asset.mock is True
    assert asset.fbx_path.exists()
    assert len(asset.texture_paths) == 1


def test_insufficient_credits_raises_clear_error(monkeypatch, tmp_path: Path):
    """Reproduces the real API response observed on 2026-10-11 with a valid
    but 0-balance key: task creation itself is rejected (not silently
    mocked, since the key IS configured -- see tripo_client.py docstring).
    """
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_real_but_broke")
    monkeypatch.setenv("PIPELINE_MOCK", "0")

    class FakeResponse:
        status_code = 400

        def json(self):
            return {"code": 2010, "status": "error", "message": "Insufficient credits",
                     "suggestion": "Please top up your account"}

    async def fake_post(self, url, headers=None, json=None):
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = tripo_client.TripoClient()
    with pytest.raises(tripo_client.TripoInsufficientCreditsError):
        asyncio.run(client.generate_humanoid("a beach surfer", "BeachSurfer01", tmp_path))


def test_missing_model_param_mapped_to_generic_error(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_real")
    monkeypatch.setenv("PIPELINE_MOCK", "0")

    class FakeResponse:
        status_code = 400

        def json(self):
            return {"code": 1004, "status": "error",
                     "message": "model is required, allowed values: P1-20260311, v3.1-20260211"}

    async def fake_post(self, url, headers=None, json=None):
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = tripo_client.TripoClient()
    with pytest.raises(tripo_client.TripoError) as excinfo:
        asyncio.run(client.generate_humanoid("a beach surfer", "BeachSurfer01", tmp_path))
    assert not isinstance(excinfo.value, tripo_client.TripoInsufficientCreditsError)


def test_successful_flow_downloads_glb(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRIPO_API_KEY", "tsk_real")
    monkeypatch.setenv("PIPELINE_MOCK", "0")

    class CreateResponse:
        status_code = 200

        def json(self):
            return {"code": 0, "data": {"task_id": "task_abc123"}}

    class TaskResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {
                "task_id": "task_abc123", "status": "success", "progress": 100,
                "output": {"model_url": "https://cdn.tripo3d.ai/output/model.glb"},
            }}

    class DownloadResponse:
        status_code = 200
        content = b"fake-glb-bytes"

        def raise_for_status(self):
            pass

    async def fake_post(self, url, headers=None, json=None):
        return CreateResponse()

    async def fake_get(self, url, headers=None):
        if url.startswith("https://cdn.tripo3d.ai"):
            return DownloadResponse()
        return TaskResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    client = tripo_client.TripoClient(poll_interval_s=0.01)
    asset = asyncio.run(client.generate_humanoid("a beach surfer", "BeachSurfer01", tmp_path))

    assert asset.mock is False
    assert asset.fbx_path.name == "BeachSurfer01.glb"
    assert asset.fbx_path.read_bytes() == b"fake-glb-bytes"
    assert asset.texture_paths == []
