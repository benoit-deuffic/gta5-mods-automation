"""Asynchronous Tripo3D API client — a second text-to-3D backend alongside
Meshy (see mcp_server/text3d_backend.py for the pluggable interface).

Verified live against the real API on 2026-10-11 (see
docs/architecture-decisions.md point 9):
- Base URL https://openapi.tripo3d.ai/v3, Bearer auth, key prefix "tsk_"
  (NOT "msy_" — this is what was in ~/.meshly, mistaken for a Meshy key).
- GET /account/balance confirmed the key authenticates (HTTP 200) but the
  account has 0 credits (balance=0.00, frozen=0.00).
- POST /generation/text-to-model without `model` returns HTTP 400 code 1004
  listing allowed values: P1-20260311, P2-20260801, v2.5-20250123,
  v3.0-20250812, v3.1-20260211 (used here as the default).
- Async task pattern: POST returns {"code":0,"data":{"task_id":...}}; poll
  GET /tasks/{task_id} until data.status == "success"|"failed"|"banned"|
  "expired"; output.model_url is a **GLB** (not FBX, no separate texture
  files — PBR maps are embedded in the GLB) and expires 5 minutes after the
  task succeeds, so it must be downloaded immediately.

Because insufficient credits is a *configured-but-broken* state (unlike "no
key at all"), this client does NOT silently fall back to mock when the key
is present — it raises TripoError with the API's own message (e.g.
"Insufficient credits") so the failure is visible instead of masked. Mock
mode only activates when TRIPO_API_KEY is absent, matching meshy_client's
policy.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

from mcp_server.meshy_client import GeneratedAsset, FIXTURES_DIR

DEFAULT_MODEL_VERSION = "v3.1-20260211"


class TripoError(RuntimeError):
    pass


class TripoInsufficientCreditsError(TripoError):
    pass


def is_tripo_mock_mode() -> bool:
    forced = os.environ.get("PIPELINE_MOCK", "auto").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    return not bool(os.environ.get("TRIPO_API_KEY", "").strip())


class TripoClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://openapi.tripo3d.ai/v3",
                 model_version: str = DEFAULT_MODEL_VERSION,
                 poll_interval_s: float = 3.0, poll_timeout_s: float = 300.0):
        self.api_key = api_key or os.environ.get("TRIPO_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def get_balance(self, client: httpx.AsyncClient) -> dict:
        resp = await client.get("/account/balance", headers=self._headers())
        resp.raise_for_status()
        return resp.json()["data"]

    async def _poll_task(self, client: httpx.AsyncClient, task_id: str) -> dict:
        elapsed = 0.0
        while elapsed < self.poll_timeout_s:
            resp = await client.get(f"/tasks/{task_id}", headers=self._headers())
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", {})
            status = data.get("status")
            if status == "success":
                return data
            if status in ("failed", "banned", "expired"):
                raise TripoError(f"Tripo task {task_id} ended with status={status!r}: {data}")
            await asyncio.sleep(self.poll_interval_s)
            elapsed += self.poll_interval_s
        raise TripoError(f"Tripo task {task_id} timed out after {self.poll_timeout_s}s")

    async def generate_humanoid(self, prompt: str, target_name: str, staging_dir: Path,
                                 pose_mode: str = "a-pose") -> GeneratedAsset:
        staging_dir.mkdir(parents=True, exist_ok=True)

        if is_tripo_mock_mode():
            return self._generate_mock(target_name, staging_dir)

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            create_resp = await client.post(
                "/generation/text-to-model",
                headers=self._headers(),
                json={"prompt": prompt, "model": self.model_version},
            )
            body = create_resp.json()
            if create_resp.status_code != 200 or body.get("code") != 0:
                message = body.get("message", str(body))
                if "credit" in message.lower() or body.get("code") == 2010:
                    raise TripoInsufficientCreditsError(
                        f"Tripo3D rejected the request: {message}. "
                        "Top up at https://platform.tripo3d.ai before retrying."
                    )
                raise TripoError(f"Tripo3D task creation failed ({create_resp.status_code}): {message}")

            task_id = body["data"]["task_id"]
            result = await self._poll_task(client, task_id)

            model_url = result.get("output", {}).get("model_url")
            if not model_url:
                raise TripoError(f"Tripo task {task_id} succeeded but has no output.model_url: {result}")

            # model_url expires 5 minutes after success -> download immediately.
            glb_path = staging_dir / f"{target_name}.glb"
            dl_resp = await client.get(model_url)
            dl_resp.raise_for_status()
            glb_path.write_bytes(dl_resp.content)

            return GeneratedAsset(fbx_path=glb_path, texture_paths=[], mock=False, task_id=task_id)

    def _generate_mock(self, target_name: str, staging_dir: Path) -> GeneratedAsset:
        fbx_fixture = FIXTURES_DIR / "mock_humanoid.fbx"
        tex_fixture = FIXTURES_DIR / "mock_texture.png"
        if not fbx_fixture.exists() or not tex_fixture.exists():
            raise TripoError("Mock fixtures missing (tests/fixtures/...); run tests/make_fixtures.py.")
        fbx_path = staging_dir / f"{target_name}.fbx"
        tex_path = staging_dir / f"{target_name}_base_color_0.png"
        shutil.copy2(fbx_fixture, fbx_path)
        shutil.copy2(tex_fixture, tex_path)
        return GeneratedAsset(fbx_path=fbx_path, texture_paths=[tex_path], mock=True, task_id="mock-task")
