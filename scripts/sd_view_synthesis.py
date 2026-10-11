"""Standalone SDXL + ControlNet-depth view synthesis step, run with the
sd-local venv's own Python (NOT Blender's bundled Python, which has no
diffusers/torch):

    ~/tools/sd-local/venv/bin/python scripts/sd_view_synthesis.py \
        --depth-image <true_depth_map.png> --prompt "<text>" --output <out.png> \
        --steps 30 --controlnet-conditioning-scale 0.8

This is invoked as a subprocess from scripts/ai_texture_reskin.py (which runs
*inside* Blender and cannot import torch/diffusers itself). Kept as a
separate, independently-runnable script so it can be tested/benchmarked on
its own, same separation-of-concerns as scripts/codewalker_convert.py being
a standalone CLI the Blender script shells out to.

Backend choice (see docs/architecture-decisions.md, point superseding #13):
SDXL base (already downloaded locally at ~/tools/sd-local/models/sdxl-base)
+ `diffusers/controlnet-depth-sdxl-1.0` ControlNet, driven by
StableDiffusionXLControlNetPipeline in TEXT-TO-IMAGE mode -- the depth map
is the ONLY structural guide (control_image), content/material/color comes
entirely from the prompt. This replaces the old img2img-on-flat-guide
approach, which needed strength>=0.85 to produce any real content and even
then looked like abstract armor rather than the requested garment (see the
pitfall note in the gtav-ped-modding skill and architecture-decisions.md
point 13) -- conditioning on image *content* with img2img fights the prompt
instead of just constraining geometry the way ControlNet-depth does.

Fallback path (--backend sd2depth): StableDiffusionDepth2ImgPipeline
(stabilityai/stable-diffusion-2-depth, SD2.1-based, ~5GB, lower res) for use
only if ControlNet-SDXL hits a real blocker on this stack. Not needed this
session -- diffusers 0.41.0 / torch 2.11.0+cu128 already installed in the
sd-local venv loaded ControlNetModel + StableDiffusionXLControlNetPipeline
with zero dependency conflicts.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_MODEL_DIR = str(Path.home() / "tools" / "sd-local" / "models" / "sdxl-base")
DEFAULT_CONTROLNET_DIR = str(Path.home() / "tools" / "sd-local" / "models" / "controlnet-depth-sdxl-1.0")
CONTROLNET_HUB_ID = "diffusers/controlnet-depth-sdxl-1.0"


def parse_args():
    p = argparse.ArgumentParser(description="SDXL ControlNet-depth single-view synthesis")
    p.add_argument("--depth-image", required=True,
                   help="Path to the TRUE per-pixel depth map PNG (grayscale, near=white/far=black) "
                        "produced by ai_texture_reskin.py's render_guide_image()")
    p.add_argument("--init-image", dest="depth_image",
                   help="Deprecated alias for --depth-image, kept for backward compatibility.")
    p.add_argument("--prompt", required=True)
    p.add_argument("--negative-prompt", default="blurry, low quality, distorted, extra limbs, text, watermark")
    p.add_argument("--output", required=True, help="Path to write the generated PNG")
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--guidance-scale", type=float, default=7.0)
    p.add_argument("--controlnet-conditioning-scale", type=float, default=0.8,
                   help="How strongly the depth map constrains geometry (0=ignore it/pure prompt, "
                        "1=follow it strictly). 0.8 lets the prompt's material/content dominate while "
                        "still respecting rough body/garment silhouette from depth.")
    p.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    p.add_argument("--controlnet-dir", default=DEFAULT_CONTROLNET_DIR,
                   help="Local snapshot dir for diffusers/controlnet-depth-sdxl-1.0. Falls back to "
                        "downloading CONTROLNET_HUB_ID from the hub if this path doesn't exist.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--width", type=int, default=0, help="0 = use depth image's own size")
    p.add_argument("--height", type=int, default=0, help="0 = use depth image's own size")
    p.add_argument("--backend", choices=["controlnet-sdxl", "sd2depth"], default="controlnet-sdxl",
                   help="controlnet-sdxl (preferred, stays on the proven SDXL/cu128 stack) or "
                        "sd2depth (StableDiffusionDepth2ImgPipeline fallback if ControlNet-SDXL hits "
                        "a real documented blocker on this machine -- not needed this session).")
    # --strength kept accepted-but-ignored so old callers/tests that still pass it don't break.
    p.add_argument("--strength", type=float, default=None, help=argparse.SUPPRESS)
    return p.parse_args()


def _run_controlnet_sdxl(args, device, dtype):
    import torch
    from diffusers import ControlNetModel, StableDiffusionXLControlNetPipeline
    from PIL import Image

    depth_image = Image.open(args.depth_image).convert("RGB")
    width = args.width or depth_image.size[0]
    height = args.height or depth_image.size[1]
    if depth_image.size != (width, height):
        depth_image = depth_image.resize((width, height), Image.LANCZOS)

    controlnet_source = args.controlnet_dir if Path(args.controlnet_dir).exists() else CONTROLNET_HUB_ID
    controlnet = ControlNetModel.from_pretrained(controlnet_source, torch_dtype=dtype)

    pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
        args.model_dir, controlnet=controlnet, torch_dtype=dtype, use_safetensors=True,
    )

    # RTX 5070 has ~12GB VRAM total; SDXL UNet + ControlNet-depth together can
    # get tight alongside VAE decode at 512-1024px. Try GPU first, fall back
    # to CPU offload (keeps everything working but noticeably slower) only on
    # an actual OOM rather than pre-emptively -- verified unnecessary at
    # 512x512 render size on this machine (comfortably fits with slicing on).
    try:
        pipe = pipe.to(device)
        pipe.enable_attention_slicing()
    except torch.cuda.OutOfMemoryError:
        print("[WARN] CUDA OOM moving pipeline to GPU, falling back to model CPU offload", file=sys.stderr)
        pipe.enable_model_cpu_offload()

    generator = torch.Generator(device=device).manual_seed(args.seed)

    try:
        result = pipe(
            prompt=args.prompt,
            negative_prompt=args.negative_prompt,
            image=depth_image,
            controlnet_conditioning_scale=args.controlnet_conditioning_scale,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            width=width,
            height=height,
            generator=generator,
        )
    except torch.cuda.OutOfMemoryError:
        print("[WARN] CUDA OOM during inference, retrying with model CPU offload", file=sys.stderr)
        torch.cuda.empty_cache()
        pipe.enable_model_cpu_offload()
        result = pipe(
            prompt=args.prompt,
            negative_prompt=args.negative_prompt,
            image=depth_image,
            controlnet_conditioning_scale=args.controlnet_conditioning_scale,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            width=width,
            height=height,
            generator=generator,
        )

    out_image = result.images[0]
    return out_image.resize(depth_image.size, Image.LANCZOS)


def _run_sd2_depth(args, device, dtype):
    """Fallback path: StableDiffusionDepth2ImgPipeline (SD2.1-based)."""
    import torch
    from diffusers import StableDiffusionDepth2ImgPipeline
    from PIL import Image

    depth_image = Image.open(args.depth_image).convert("RGB")

    pipe = StableDiffusionDepth2ImgPipeline.from_pretrained(
        "stabilityai/stable-diffusion-2-depth", torch_dtype=dtype,
    )
    pipe = pipe.to(device)
    try:
        pipe.enable_attention_slicing()
    except Exception:
        pass

    generator = torch.Generator(device=device).manual_seed(args.seed)
    result = pipe(
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        image=depth_image,
        strength=0.9,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        generator=generator,
    )
    out_image = result.images[0]
    return out_image.resize(depth_image.size, Image.LANCZOS)


def main():
    args = parse_args()

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    if args.backend == "sd2depth":
        out_image = _run_sd2_depth(args, device, dtype)
    else:
        out_image = _run_controlnet_sdxl(args, device, dtype)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out_image.save(args.output)
    print(f"[OK] wrote {args.output} ({out_image.size[0]}x{out_image.size[1]}) backend={args.backend}")


if __name__ == "__main__":
    main()
