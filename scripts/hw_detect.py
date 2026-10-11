"""Hardware detection used by scripts/bootstrap_ollama.sh and
scripts/bootstrap_local_3d_env.sh to pick model/tooling tiers that fit the
host actually running the script (this dev sandbox has no GPU and ~1.9GB RAM;
the future dedicated workstation has an RTX 5070 12GB + presumably far more
RAM — same script, different tier picked automatically).

Pure stdlib, no deps, so it's trivially unit-testable and usable from shell
via `python3 scripts/hw_detect.py --json`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass


@dataclass
class HostProfile:
    has_nvidia_gpu: bool
    gpu_vram_mb: int | None
    total_ram_mb: int
    free_disk_mb: int
    tier: str  # "gpu-large" | "gpu-small" | "cpu-medium" | "cpu-low"


def _read_nvidia_vram_mb() -> int | None:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return None
    try:
        out = subprocess.run(
            [nvidia_smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        first_line = out.stdout.strip().splitlines()[0]
        return int(float(first_line.strip()))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def _read_total_ram_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"MemTotal:\s+(\d+)\s*kB", line)
                if m:
                    return int(m.group(1)) // 1024
    except OSError:
        pass
    return 0


def _read_free_disk_mb(path: str = "/") -> int:
    usage = shutil.disk_usage(path)
    return usage.free // (1024 * 1024)


def detect_host_profile(disk_path: str = "/") -> HostProfile:
    vram_mb = _read_nvidia_vram_mb()
    has_gpu = vram_mb is not None and vram_mb > 0
    total_ram_mb = _read_total_ram_mb()
    free_disk_mb = _read_free_disk_mb(disk_path)

    if has_gpu and vram_mb >= 10000:
        tier = "gpu-large"
    elif has_gpu and vram_mb >= 5000:
        tier = "gpu-small"
    elif total_ram_mb >= 8000:
        tier = "cpu-medium"
    else:
        tier = "cpu-low"

    return HostProfile(
        has_nvidia_gpu=has_gpu,
        gpu_vram_mb=vram_mb,
        total_ram_mb=total_ram_mb,
        free_disk_mb=free_disk_mb,
        tier=tier,
    )


# Model picks per tier. Kept deliberately small for "cpu-low" since this dev
# sandbox (1.9GB RAM, ~8.7GB free disk) must survive running the bootstrap
# script without starving the rest of the project.
OLLAMA_MODEL_TIERS: dict[str, dict[str, str | None]] = {
    "gpu-large": {"text": "qwen2.5:14b-instruct", "vision": "llava:13b"},
    "gpu-small": {"text": "qwen2.5:7b-instruct", "vision": "llava:7b"},
    "cpu-medium": {"text": "qwen2.5:3b-instruct", "vision": None},
    "cpu-low": {"text": "qwen2.5:0.5b-instruct", "vision": None},
}


def recommended_ollama_models(profile: HostProfile) -> dict[str, str | None]:
    return OLLAMA_MODEL_TIERS[profile.tier]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--disk-path", default="/")
    args = parser.parse_args()

    profile = detect_host_profile(args.disk_path)
    models = recommended_ollama_models(profile)

    if args.json:
        print(json.dumps({"profile": asdict(profile), "ollama_models": models}, indent=2))
    else:
        print(f"tier={profile.tier} gpu={profile.has_nvidia_gpu} "
              f"vram_mb={profile.gpu_vram_mb} ram_mb={profile.total_ram_mb} "
              f"free_disk_mb={profile.free_disk_mb}")
        print(f"recommended ollama models: {models}")


if __name__ == "__main__":
    main()
