"""Standalone SDXL img2img view synthesis step, run with the sd-local venv's
own Python (NOT Blender's bundled Python, which has no diffusers/torch):

    ~/tools/sd-local/venv/bin/python scripts/sd_view_synthesis.py \
        --init-image <depth_guide.png> --prompt "<text>" --output <out.png> \
        --strength 0.6 --steps 30

This is invoked as a subprocess from scripts/ai_texture_reskin.py (which runs
*inside* Blender and cannot import torch/diffusers itself). Kept as a
separate, independently-runnable script so it can be tested/benchmarked on
its own, same separation-of-concerns as scripts/codewalker_convert.py being
a standalone CLI the Blender script shells out to.

Backend choice: SDXL img2img (model already downloaded locally at
~/tools/sd-local/models/sdxl-base, proven working on this GPU), NOT
StableDiffusionDepth2ImgPipeline. stable-diffusion-2-depth would need a
~5GB fresh download and was not validated this session -- SDXL img2img with
the depth/flat-shaded render as the init image was explicitly allowed as the
fallback path by the task and is what's implemented + tested here.
Honest caveat (see docs/architecture-decisions.md): img2img conditions on
overall image structure/luminance, not true per-pixel depth the way a
ControlNet-depth or Depth2Img model would -- it will NOT preserve fine
geometric detail as faithfully. It was chosen because it works today with
zero extra downloads; swapping in a proper depth ControlNet is a known
follow-up, not implemented here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_MODEL_DIR = str(Path.home() / "tools" / "sd-local" / "models" / "sdxl-base")


def parse_args():
    p = argparse.ArgumentParser(description="SDXL img2img single-view synthesis")
    p.add_argument("--init-image", required=True, help="Path to the depth/flat-shaded guide PNG")
    p.add_argument("--prompt", required=True)
    p.add_argument("--negative-prompt", default="blurry, low quality, distorted, extra limbs, text, watermark")
    p.add_argument("--output", required=True, help="Path to write the generated PNG")
    p.add_argument("--strength", type=float, default=0.6)
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--guidance-scale", type=float, default=7.0)
    p.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main():
    args = parse_args()

    import torch
    from diffusers import StableDiffusionXLImg2ImgPipeline
    from PIL import Image

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
        args.model_dir, torch_dtype=dtype, use_safetensors=True,
    )
    pipe = pipe.to(device)
    try:
        pipe.enable_attention_slicing()
    except Exception:
        pass

    init_image = Image.open(args.init_image).convert("RGB")
    generator = torch.Generator(device=device).manual_seed(args.seed)

    result = pipe(
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        image=init_image,
        strength=args.strength,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        generator=generator,
    )
    out_image = result.images[0]
    out_image = out_image.resize(init_image.size, Image.LANCZOS)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out_image.save(args.output)
    print(f"[OK] wrote {args.output} ({out_image.size[0]}x{out_image.size[1]})")


if __name__ == "__main__":
    main()
