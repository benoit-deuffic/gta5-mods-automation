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


def _fake_ollama(monkeypatch, response_text):
    monkeypatch.setattr(prompt_refiner, "is_ollama_available", lambda endpoint: True)
    sent = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": response_text}

    def fake_post(url, json, timeout):
        sent.update(json)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def _no_network(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("network reached")
    monkeypatch.setattr(httpx, "post", fail)


def test_infer_body_targets_reads_explicit_targets_without_llm(monkeypatch):
    _no_network(monkeypatch)
    result = prompt_refiner.infer_body_targets("bonnet 80a, fesses bombées", model="m")
    assert (result.bra_size, result.glutes, result.glute_height, result.model) == ("80A", "round", None, None)
    result = prompt_refiner.infer_body_targets("grosses fesses, fesses tombantes", model="m")
    assert (result.glutes, result.glute_height) == ("large", "low")
    assert prompt_refiner.infer_body_targets("big butt, perky butt", model="m").glute_height == "high"
    for text, shape in (("grosses fesses", "large"), ("un fessier rebondi", "round"),
                        ("un derrière plat", "slim"), ("athletic butt", "athletic")):
        assert prompt_refiner.infer_body_targets(text, model="m").glutes == shape


def test_infer_body_targets_asks_llm_only_for_mentioned_missing_parts(monkeypatch):
    sent = _fake_ollama(monkeypatch, json.dumps({"bra_size": " b ", "glutes": "large", "glute_height": "low"}))
    # glutes read from text; the LLM's glutes answer is ignored, its bra size used
    result = prompt_refiner.infer_body_targets("petite poitrine, fesses athlétiques", model="m")
    assert (result.bra_size, result.glutes, result.model) == ("B", "athletic", "m")
    assert sent["format"]["required"] == ["bra_size", "glutes", "glute_height"]
    assert result.glute_height is None  # no height cue in the text: LLM's answer ignored

    # height cue present but not next to the noun -> asked to the LLM
    _fake_ollama(monkeypatch, json.dumps({"bra_size": None, "glutes": "round", "glute_height": "high"}))
    result = prompt_refiner.infer_body_targets("des fesses rondes et bien hautes", model="m")
    assert (result.glutes, result.glute_height) == ("round", "high")

    # a part the text doesn't mention is never taken from the LLM
    _fake_ollama(monkeypatch, json.dumps({"bra_size": "E", "glutes": "round"}))
    result = prompt_refiner.infer_body_targets("des hanches généreuses", model="m")
    assert (result.bra_size, result.glutes) == (None, "round")

    # no band in the description -> an LLM-invented band is dropped
    _fake_ollama(monkeypatch, json.dumps({"bra_size": "80AA", "glutes": None}))
    assert prompt_refiner.infer_body_targets("petite poitrine", model="m").bra_size == "AA"
    _fake_ollama(monkeypatch, json.dumps({"bra_size": "85C", "glutes": None}))
    assert prompt_refiner.infer_body_targets("tour 85, poitrine moyenne", model="m").bra_size == "85C"

    for bad in (json.dumps({"bra_size": "huge", "glutes": None}), json.dumps({"bra_size": 3, "glutes": None}),
                json.dumps({"bra_size": None, "glutes": "enormous"}),
                json.dumps({"bra_size": None, "glutes": None, "glute_height": "very high"}), "not json"):
        _fake_ollama(monkeypatch, bad)
        with pytest.raises(prompt_refiner.PromptRefinerError):
            prompt_refiner.infer_body_targets("petite poitrine et hanches larges, plutôt hautes", model="m")


def test_infer_body_targets_raises_instead_of_mocking(monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(prompt_refiner.PromptRefinerError):  # nothing about bust or glutes
        prompt_refiner.infer_body_targets("cheveux longs", model="m")
    _fake_ollama(monkeypatch, json.dumps({"bra_size": None, "glutes": None}))
    with pytest.raises(prompt_refiner.PromptRefinerError):  # mentioned but nothing extracted
        prompt_refiner.infer_body_targets("une jolie poitrine", model="m")
    monkeypatch.setattr(prompt_refiner, "is_ollama_available", lambda endpoint: False)
    with pytest.raises(prompt_refiner.PromptRefinerError):
        prompt_refiner.infer_body_targets("petite poitrine", model="m")


def test_infer_body_targets_blocks_before_network(monkeypatch):
    _no_network(monkeypatch)
    with pytest.raises(prompt_refiner.PromptBlockedError):
        prompt_refiner.infer_body_targets("underage body 80A", model="m")
