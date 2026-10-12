import json
from pathlib import Path

import httpx
import pytest

from mcp_server import prompt_refiner


def test_blocked_keyword_raises_before_any_network_call(monkeypatch):
    # Hard guard scope is strictly child-protection + fetish content, NOT
    # general nudity/explicit content (nudity is in-scope for this pipeline,
    # see user decision 2026-10-12 -- "ce n'en est plus une sauf les termes
    # enfants, fetish"). Covered with "underage" instead of "nude".
    def fail_if_called(*a, **kw):
        raise AssertionError("should not reach network when blocked")
    monkeypatch.setattr(httpx, "get", fail_if_called)
    monkeypatch.setattr(httpx, "post", fail_if_called)

    with pytest.raises(prompt_refiner.PromptBlockedError):
        prompt_refiner.refine_prompt("an underage character")


def test_falls_back_to_original_when_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(prompt_refiner, "is_ollama_available", lambda endpoint: False)
    result = prompt_refiner.refine_prompt("a beach surfer", model="qwen2.5:0.5b-instruct")
    assert result.mock is True
    assert result.refined_prompt == "a beach surfer"


def test_falls_back_when_no_model_configured(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(prompt_refiner, "CONFIG_PATH", tmp_path / "missing_ollama.json")
    monkeypatch.delenv("OLLAMA_TEXT_MODEL", raising=False)
    result = prompt_refiner.refine_prompt("a beach surfer")
    assert result.mock is True
    assert result.model is None


def test_uses_real_response_when_ollama_reachable(monkeypatch):
    monkeypatch.setattr(prompt_refiner, "is_ollama_available", lambda endpoint: True)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "a detailed SFW beach surfer description"}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: FakeResponse())

    result = prompt_refiner.refine_prompt("a beach surfer", model="qwen2.5:0.5b-instruct")
    assert result.mock is False
    assert result.refined_prompt == "a detailed SFW beach surfer description"


def test_blocked_keyword_in_llm_output_is_caught(monkeypatch):
    # Same scope note as above: the LLM-output re-check only catches
    # child-protection/fetish terms, not general nudity.
    monkeypatch.setattr(prompt_refiner, "is_ollama_available", lambda endpoint: True)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "a fetish outfit on a minor"}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: FakeResponse())

    with pytest.raises(prompt_refiner.PromptBlockedError):
        prompt_refiner.refine_prompt("a beach surfer", model="qwen2.5:0.5b-instruct")
