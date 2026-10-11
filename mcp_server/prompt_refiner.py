"""Local LLM prompt refinement via Ollama (replaces no 3D generation; see
docs/architecture-decisions.md point 7 for why Ollama != a Meshy replacement).

Takes the user's raw, possibly terse prompt and asks a local Ollama model to
turn it into a detailed, SFW, Meshy-friendly humanoid description (clothing,
build, pose cues) before it's handed to the 3D backend. Falls back to
returning the original prompt unchanged (mock=True) when Ollama isn't
reachable or the configured model isn't pulled -- it never blocks the
pipeline on an optional enrichment step.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "ollama.json"

_SYSTEM_PROMPT = (
    "You are a prompt engineer for a text-to-3D humanoid character generator. "
    "Rewrite the user's short description into a single detailed paragraph "
    "covering: body build, clothing/outfit, hairstyle, and a neutral standing "
    "A-pose. Keep it strictly safe-for-work (no nudity, no sexual content, no "
    "depictions of real people). Output ONLY the rewritten prompt, no preamble."
)

# A hard keyword guard in addition to the system prompt: refuse to forward
# obviously NSFW/illegal requests to the LLM at all (see
# docs/architecture-decisions.md point 5 on Meshy/NSFW policy, which applies
# to this local step too since it feeds the same downstream generator).
_BLOCKED_KEYWORDS = (
    "nude", "naked", "nsfw", "sex", "porn", "explicit", "fetish", "underage", "minor",
)


class PromptRefinerError(RuntimeError):
    pass


class PromptBlockedError(PromptRefinerError):
    pass


@dataclass
class RefinedPrompt:
    original_prompt: str
    refined_prompt: str
    mock: bool
    model: str | None = None


def _load_ollama_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _check_blocked(prompt: str) -> None:
    lowered = prompt.lower()
    hit = next((kw for kw in _BLOCKED_KEYWORDS if kw in lowered), None)
    if hit:
        raise PromptBlockedError(
            f"Prompt rejected: contains blocked term '{hit}'. This pipeline only "
            "supports SFW humanoid generation (see docs/architecture-decisions.md point 5)."
        )


def is_ollama_available(endpoint: str) -> bool:
    try:
        resp = httpx.get(f"{endpoint}/api/version", timeout=3.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def refine_prompt(raw_prompt: str, endpoint: str | None = None, model: str | None = None) -> RefinedPrompt:
    _check_blocked(raw_prompt)

    cfg = _load_ollama_config()
    endpoint = endpoint or os.environ.get("OLLAMA_ENDPOINT") or cfg.get("endpoint", "http://127.0.0.1:11434")
    model = model or os.environ.get("OLLAMA_TEXT_MODEL") or cfg.get("text_model")

    if not model or not is_ollama_available(endpoint):
        return RefinedPrompt(original_prompt=raw_prompt, refined_prompt=raw_prompt, mock=True, model=model)

    try:
        resp = httpx.post(
            f"{endpoint}/api/generate",
            json={
                "model": model,
                "system": _SYSTEM_PROMPT,
                "prompt": raw_prompt,
                "stream": False,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        refined = resp.json().get("response", "").strip()
    except httpx.HTTPError as exc:
        # Ollama reachable at /api/version but generate failed (model missing, OOM...):
        # degrade gracefully instead of failing the whole pipeline.
        return RefinedPrompt(original_prompt=raw_prompt, refined_prompt=raw_prompt, mock=True, model=model)

    if not refined:
        return RefinedPrompt(original_prompt=raw_prompt, refined_prompt=raw_prompt, mock=True, model=model)

    # The refiner output itself could drift into disallowed territory; re-check.
    _check_blocked(refined)

    return RefinedPrompt(original_prompt=raw_prompt, refined_prompt=refined, mock=False, model=model)
