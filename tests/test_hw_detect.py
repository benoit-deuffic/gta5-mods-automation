from pathlib import Path

import pytest

from scripts import hw_detect


def test_detect_host_profile_cpu_low_without_gpu(monkeypatch):
    monkeypatch.setattr(hw_detect, "_read_nvidia_vram_mb", lambda: None)
    monkeypatch.setattr(hw_detect, "_read_total_ram_mb", lambda: 2000)
    monkeypatch.setattr(hw_detect, "_read_free_disk_mb", lambda path="/": 8000)

    profile = hw_detect.detect_host_profile()
    assert profile.tier == "cpu-low"
    assert profile.has_nvidia_gpu is False

    models = hw_detect.recommended_ollama_models(profile)
    assert models["text"] == "qwen2.5:0.5b-instruct"
    assert models["vision"] is None


def test_detect_host_profile_gpu_large(monkeypatch):
    monkeypatch.setattr(hw_detect, "_read_nvidia_vram_mb", lambda: 12000)
    monkeypatch.setattr(hw_detect, "_read_total_ram_mb", lambda: 32000)
    monkeypatch.setattr(hw_detect, "_read_free_disk_mb", lambda path="/": 200000)

    profile = hw_detect.detect_host_profile()
    assert profile.tier == "gpu-large"
    assert profile.has_nvidia_gpu is True

    models = hw_detect.recommended_ollama_models(profile)
    assert models["text"] == "qwen2.5:14b-instruct"
    assert models["vision"] == "llava:13b"


def test_detect_host_profile_gpu_small(monkeypatch):
    monkeypatch.setattr(hw_detect, "_read_nvidia_vram_mb", lambda: 8000)
    monkeypatch.setattr(hw_detect, "_read_total_ram_mb", lambda: 16000)
    monkeypatch.setattr(hw_detect, "_read_free_disk_mb", lambda path="/": 100000)

    profile = hw_detect.detect_host_profile()
    assert profile.tier == "gpu-small"


def test_detect_host_profile_cpu_medium_without_gpu(monkeypatch):
    monkeypatch.setattr(hw_detect, "_read_nvidia_vram_mb", lambda: None)
    monkeypatch.setattr(hw_detect, "_read_total_ram_mb", lambda: 16000)
    monkeypatch.setattr(hw_detect, "_read_free_disk_mb", lambda path="/": 50000)

    profile = hw_detect.detect_host_profile()
    assert profile.tier == "cpu-medium"
