"""Local LLM prompt refinement via Ollama (replaces no 3D generation; see
docs/architecture-decisions.md point 7 for why Ollama isn't a text3d_backend
replacement).

Takes the user's raw, possibly terse prompt and asks a local Ollama model to
turn it into a detailed, SFW, backend-agnostic humanoid description (clothing,
build, pose cues) before it's handed to the configured text3d_backend (see
mcp_server/text3d_backend.py). Falls back to returning the original prompt
unchanged (mock=True) when Ollama isn't reachable or the configured model
isn't pulled -- it never blocks the pipeline on an optional enrichment step.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from scripts.body_morphology import GLUTE_HEIGHTS, GLUTE_SHAPES, parse_bra_size

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "ollama.json"

_SYSTEM_PROMPT = (
    "You are a prompt engineer for a text-to-3D humanoid character generator. "
    "Rewrite the user's short description into a single detailed paragraph "
    "covering: body build, clothing/outfit, hairstyle, and a neutral standing "
    "A-pose. Keep it strictly safe-for-work (no nudity, no sexual content, no "
    "depictions of real people). Output ONLY the rewritten prompt, no preamble."
)

# A hard keyword guard in addition to the system prompt: refuse to forward
# obviously illegal content at all (child-protection + fetish only -- NOT a
# general NSFW/nudity filter; user decision 2026-10-12: nudity/explicit
# content is in-scope for the local SDXL reskin path, this guard exists only
# to hard-block child-related and fetish content regardless of destination).
# Still shared by both consumers: refine_prompt() (feeds the configured
# text3d_backend -- e.g. Tripo3D, an external third-party API with its OWN
# separate SFW-only ToS this guard does NOT enforce -- the backend may still
# reject/moderate nudity prompts on its own) and generate_ai_texture_reskin's
# prompt/skin_prompt (feeds the local SDXL pipeline only, see
# mcp_server/server.py).
_BLOCKED_KEYWORDS = (
    "fetish", "underage", "minor",
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


def _resolve_endpoint_model(endpoint: str | None, model: str | None) -> tuple[str, str | None]:
    cfg = _load_ollama_config()
    endpoint = endpoint or os.environ.get("OLLAMA_ENDPOINT") or cfg.get("endpoint", "http://127.0.0.1:11434")
    model = model or os.environ.get("OLLAMA_TEXT_MODEL") or cfg.get("text_model")
    return endpoint, model


def refine_prompt(raw_prompt: str, endpoint: str | None = None, model: str | None = None) -> RefinedPrompt:
    _check_blocked(raw_prompt)

    endpoint, model = _resolve_endpoint_model(endpoint, model)

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


# --- Body description -> measurable body targets ---------------------------
# The LLM no longer guesses slider values (they were uncalibrated: qwen's
# "80A" from a declared "100D" removed 1.3 cm of bust on a body measured at
# 80G, see docs/architecture-decisions.md). It only turns the request into
# targets with a measurable definition -- an EU bra size, a glute shape of
# body_morphology.GLUTE_SHAPES, a glute height of GLUTE_HEIGHTS -- and
# Blender MEASURES the body and fits it
# (body_morphology.fit_bust / fit_glutes). Unlike refine_prompt() this has
# NO mock fallback: silently returning "no target" would build an unchanged
# ped while the user asked for a different body.

@dataclass
class BodyTargets:
    bra_size: str | None  # validated by body_morphology.parse_bra_size, e.g. "80A" or "B"
    glutes: str | None    # a body_morphology.GLUTE_SHAPES key
    glute_height: str | None  # a body_morphology.GLUTE_HEIGHTS key
    model: str | None     # None when everything was read directly from the text (no LLM)


_BRA_SIZE_IN_TEXT = re.compile(r"\b(\d{2,3}\s?(?:AA|[A-H]))\b", re.IGNORECASE)
# Explicit shape words next to the body part, FR (both word orders) and EN.
_GLUTE_WORDS = {
    "slim": r"minces?|plat(?:e)?s?|slim|flat|small",
    "athletic": r"athl[ée]tiques?|muscl[ée]e?s?|sporti(?:f|ve)s?|athletic|toned|muscular",
    "round": r"bomb[ée]e?s?|rebondie?s?|ronde?s?|round|bubble",
    "large": r"gros(?:se)?s?|grande?s?|volumineu(?:x|se)s?|[ée]normes?|large|big|huge",
}
_GLUTE_NOUN = r"(?:fesses?|fessiers?|cul|derri[èe]re|popotin|glutes?|butt|buttocks|booty|bum)"
_GLUTE_HEIGHT_WORDS = {
    "high": r"hautes?|haut\s+perch[ée]e?s?|remont[ée]e?s?|high|perky|lifted",
    "low": r"basses?|tombante?s?|affaiss[ée]e?s?|low|saggy|droopy",
}


def _next_to_noun(words: str) -> re.Pattern:
    return re.compile(rf"\b(?:(?:{words})\s+{_GLUTE_NOUN}|{_GLUTE_NOUN}\s+(?:{words}))\b", re.IGNORECASE)


_GLUTE_IN_TEXT = {shape: _next_to_noun(words) for shape, words in _GLUTE_WORDS.items()}
_GLUTE_HEIGHT_IN_TEXT = {height: _next_to_noun(words) for height, words in _GLUTE_HEIGHT_WORDS.items()}
# An LLM value is only accepted when the text talks about that body part at
# all, so it can't add a change nobody asked for.
_MENTIONS = {
    "bra_size": re.compile(r"\b(?:poitrine|seins?|bonnets?|soutien|bust|breasts?|boobs?|chest|cups?)\b",
                           re.IGNORECASE),
    "glutes": re.compile(rf"\b(?:{_GLUTE_NOUN}|hanches?|hips?)\b", re.IGNORECASE),
    # a height cue AND a glute mention (checked together in _mentioned)
    "glute_height": re.compile(r"\b(?:haut\w*|bas(?:se)?s?|tomb\w*|remont\w*|perch\w*|affaiss\w*|"
                               r"milieu|high|low|perky|lifted|sagg\w*|droop\w*)\b", re.IGNORECASE),
}


def _mentioned(key: str, description: str) -> bool:
    if key == "glute_height" and not _MENTIONS["glutes"].search(description):
        return False
    return bool(_MENTIONS[key].search(description))

_TARGETS_SYSTEM_PROMPT = (
    "You extract body targets from a body description for a GTA V female ped. "
    "bra_size: the requested breast size as a European (EN 13402) bra size, "
    "optional band (underbust cm, e.g. 75, 80, 85) + cup letter AA, A, B, C, D, "
    "E, F, G or H; without an explicit band give the cup letter alone; vague "
    "words: very small/flat = AA, small = A, medium = C, large = E, very large = G. "
    "glutes: the requested buttock shape, one of slim (thin/flat), athletic "
    "(toned/muscular), round (curvy/bubble), large (big). "
    "glute_height: where the buttocks' most prominent point sits, one of high "
    "(lifted/perky), mid, low (sagging). Use null for anything the description "
    "does not mention. "
    'Answer with JSON only: {"bra_size": "80A" or null, "glutes": "round" or null, '
    '"glute_height": "high" or null}.'
)

_TARGETS_SCHEMA = {
    "type": "object",
    "properties": {
        "bra_size": {"type": ["string", "null"]},
        "glutes": {"type": ["string", "null"], "enum": [*GLUTE_SHAPES, None]},
        "glute_height": {"type": ["string", "null"], "enum": [*GLUTE_HEIGHTS, None]},
    },
    "required": ["bra_size", "glutes", "glute_height"],
}


def infer_body_targets(description: str, endpoint: str | None = None,
                       model: str | None = None) -> BodyTargets:
    """Body targets requested by `description`, e.g. "bonnet 80A, fesses
    bombées" -> bra_size "80A", glutes "round". Explicit sizes/shapes are
    read directly (deterministic); the local Ollama model is only asked for
    what's still missing, and its untrusted answer is validated. Raises if
    the description yields no target at all."""
    _check_blocked(description)
    bra = _BRA_SIZE_IN_TEXT.search(description)
    targets = {
        "bra_size": _normalized_bra_size(bra.group(1)) if bra else None,
        "glutes": next((s for s, rx in _GLUTE_IN_TEXT.items() if rx.search(description)), None),
        "glute_height": next((h for h, rx in _GLUTE_HEIGHT_IN_TEXT.items() if rx.search(description)), None),
    }
    used_model = None
    missing = [k for k, v in targets.items() if v is None and _mentioned(k, description)]
    if missing:
        payload = _ask_targets(description, endpoint, model)
        used_model = payload["_model"]
        for key in missing:
            targets[key] = _validated(key, payload.get(key), description)
    if not any(targets.values()):
        raise PromptRefinerError(f"no body target (bra size, glute shape/height) found in {description!r}")
    return BodyTargets(bra_size=targets["bra_size"], glutes=targets["glutes"],
                       glute_height=targets["glute_height"], model=used_model)


def _ask_targets(description, endpoint, model) -> dict:
    endpoint, model = _resolve_endpoint_model(endpoint, model)
    if not model or not is_ollama_available(endpoint):
        raise PromptRefinerError(f"Ollama not reachable at {endpoint} or no text model configured")
    try:
        resp = httpx.post(
            f"{endpoint}/api/generate",
            json={
                "model": model,
                "system": _TARGETS_SYSTEM_PROMPT,
                "prompt": description,
                "format": _TARGETS_SCHEMA,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        payload = json.loads(resp.json().get("response", ""))
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise PromptRefinerError(f"body target extraction failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise PromptRefinerError(f"LLM returned no target object: {payload!r}")
    return {**payload, "_model": model}


def _validated(key: str, raw, description: str) -> str | None:
    if raw is None:
        return None
    if key in ("glutes", "glute_height"):
        allowed = GLUTE_SHAPES if key == "glutes" else GLUTE_HEIGHTS
        if raw not in allowed:
            raise PromptRefinerError(f"LLM returned an unknown {key}: {raw!r}")
        return raw
    if not isinstance(raw, str):
        raise PromptRefinerError(f"LLM returned a non-text bra size: {raw!r}")
    size = _normalized_bra_size(raw)
    if not re.search(r"\d", description):
        # the description names no band, so any band in the answer is made
        # up (qwen2.5: "une petite poitrine discrète" -> "80AA"); keep the
        # body's own measured band
        size = size.lstrip("0123456789")
    return size


def _normalized_bra_size(raw: str) -> str:
    try:
        band, cup = parse_bra_size(raw)
    except ValueError as exc:
        raise PromptRefinerError(str(exc)) from exc
    return f"{band or ''}{cup}"


def main(argv: list[str] | None = None) -> int:
    """`python -m mcp_server.prompt_refiner body "bonnet 80A, fesses bombées"`
    prints the scripts/import_layered_ped.py arguments on stdout, e.g.
    "--bust-size 80A --glutes round" (+ "--glute-height high" if asked)."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m mcp_server.prompt_refiner")
    sub = parser.add_subparsers(dest="command", required=True)
    body = sub.add_parser("body", help="body description -> --bust-size/--glutes/--glute-height arguments")
    body.add_argument("description")
    args = parser.parse_args(argv)

    try:
        result = infer_body_targets(args.description)
    except PromptRefinerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"[{result.model or 'read from text'}]", file=sys.stderr)
    print(" ".join(f"{flag} {value}" for flag, value in (("--bust-size", result.bra_size),
                                                          ("--glutes", result.glutes),
                                                          ("--glute-height", result.glute_height)) if value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
