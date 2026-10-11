"""Asynchronous Meshy API client with transparent mock fallback.

Corrections vs. the spec (docs/analyse-prerequis.md §2.5):
- Real endpoints: POST /openapi/v2/text-to-3d (mode=preview, then mode=refine
  with preview_task_id), POST /openapi/v1/rigging. Base URL has no "/v2.0"
  suffix (that was wrong in the spec's .env.example).
- `art_style` is deprecated on Meshy 6+ (values would silently be ignored);
  kept here as an optional passthrough but not required.
- NSFW: Meshy's ToS forbid NSFW content; this client does not attempt to
  bypass moderation and assumes SFW prompts only (see
  docs/architecture-decisions.md point 5).

Mock mode activates automatically when MESHY_API_KEY is empty/unset, or when
PIPELINE_MOCK=1. In mock mode, `generate_raw_3d_asset` copies small fixture
files from tests/fixtures/ instead of calling the network, so the rest of the
pipeline (Blender step, manifest, packaging) can be exercised end-to-end
without any credentials.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@dataclass
class GeneratedAsset:
    fbx_path: Path
    texture_paths: list[Path]
    mock: bool
    task_id: str | None = None


class MeshyError(RuntimeError):
    pass


def is_mock_mode() -> bool:
    forced = os.environ.get("PIPELINE_MOCK", "auto").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    return not bool(os.environ.get("MESHY_API_KEY", "").strip())


class MeshyClient:
    def __init__(self, api_key: str | None = None, base_url: str = "https://api.meshy.ai",
                 poll_interval_s: float = 5.0, poll_timeout_s: float = 900.0):
        self.api_key = api_key or os.environ.get("MESHY_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _poll_task(self, client: httpx.AsyncClient, status_url: str) -> dict:
        elapsed = 0.0
        while elapsed < self.poll_timeout_s:
            resp = await client.get(status_url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "SUCCEEDED":
                return data
            if status == "FAILED":
                raise MeshyError(f"Meshy task failed: {data}")
            await asyncio.sleep(self.poll_interval_s)
            elapsed += self.poll_interval_s
        raise MeshyError(f"Meshy task timed out after {self.poll_timeout_s}s")

    async def generate_humanoid(self, prompt: str, target_name: str, staging_dir: Path,
                                 pose_mode: str = "a-pose") -> GeneratedAsset:
        staging_dir.mkdir(parents=True, exist_ok=True)

        if is_mock_mode():
            return self._generate_mock(target_name, staging_dir)

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            preview_resp = await client.post(
                "/openapi/v2/text-to-3d",
                headers=self._headers(),
                json={"mode": "preview", "prompt": prompt, "pose_mode": pose_mode},
            )
            preview_resp.raise_for_status()
            preview_task_id = preview_resp.json()["result"]
            preview_data = await self._poll_task(client, f"/openapi/v2/text-to-3d/{preview_task_id}")

            refine_resp = await client.post(
                "/openapi/v2/text-to-3d",
                headers=self._headers(),
                json={"mode": "refine", "preview_task_id": preview_task_id, "enable_pbr": True},
            )
            refine_resp.raise_for_status()
            refine_task_id = refine_resp.json()["result"]
            refine_data = await self._poll_task(client, f"/openapi/v2/text-to-3d/{refine_task_id}")

            model_urls = refine_data.get("model_urls", {})
            fbx_url = model_urls.get("fbx")
            if not fbx_url:
                raise MeshyError(f"No FBX URL in refine result: {refine_data}")

            fbx_path = staging_dir / f"{target_name}.fbx"
            await self._download(client, fbx_url, fbx_path)

            texture_paths = []
            for i, tex in enumerate(refine_data.get("texture_urls", [{}])):
                for key in ("base_color", "metallic", "roughness", "normal"):
                    url = tex.get(key)
                    if not url:
                        continue
                    tex_path = staging_dir / f"{target_name}_{key}_{i}.png"
                    await self._download(client, url, tex_path)
                    texture_paths.append(tex_path)

            return GeneratedAsset(fbx_path=fbx_path, texture_paths=texture_paths,
                                   mock=False, task_id=refine_task_id)

    async def _download(self, client: httpx.AsyncClient, url: str, dest: Path) -> None:
        resp = await client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)

    def _generate_mock(self, target_name: str, staging_dir: Path) -> GeneratedAsset:
        fbx_fixture = FIXTURES_DIR / "mock_humanoid.fbx"
        tex_fixture = FIXTURES_DIR / "mock_texture.png"
        if not fbx_fixture.exists() or not tex_fixture.exists():
            raise MeshyError(
                "Mock fixtures missing (tests/fixtures/mock_humanoid.fbx, mock_texture.png). "
                "Run tests/make_fixtures.py or provide a MESHY_API_KEY to disable mock mode."
            )
        fbx_path = staging_dir / f"{target_name}.fbx"
        tex_path = staging_dir / f"{target_name}_base_color_0.png"
        shutil.copy2(fbx_fixture, fbx_path)
        shutil.copy2(tex_fixture, tex_path)
        return GeneratedAsset(fbx_path=fbx_path, texture_paths=[tex_path], mock=True, task_id="mock-task")
