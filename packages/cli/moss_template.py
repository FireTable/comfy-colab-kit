"""MOSS-Image DeBand & Multi-Profile API Workflow Templates.

Provides pre-configured Txt2Img and Img2Img API prompt graphs for:
- moss-image: 25-node MOSS-Image DeBand pipeline with CustomCombo Preset selection,
  anti-banding LoRAs (L37-41 & L32-36), and fast ER-SDE sampling.
- sd-latest: Official Stable Diffusion 3.5 Large fp8 pipeline.
"""
import copy
import json
import random

PRESETS = [
    "Quality \u00b7 banding fix \u00b7 25 steps",
    "Quality+ \u00b7 stronger fix (layers 32-41) \u00b7 25 steps",
    "Balanced \u00b7 fix + lodestones DMD 0.28 \u00b7 14 steps",
    "Fast \u00b7 fix + lodestones DMD 0.35 \u00b7 10 steps",
    "Fast alt \u00b7 fix + darask0 0.35 \u00b7 10 steps",
    "Off \u00b7 plain SFT (banding) \u00b7 25 steps",
]

_TEMPLATE_JSON = '{"1": {"class_type": "CustomCombo", "inputs": {"choice": "Quality \\u00b7 banding fix \\u00b7 25 steps", "index": 0}}, "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "moss_image_sft_bf16.safetensors", "weight_dtype": "default"}}, "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3.5_9b_int8_convrot.safetensors", "type": "moss_image", "device": "default"}}, "5": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}}, "6": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "A cozy anime illustration of a beautiful, cute woman in her early thirties with a graceful adult face and figure, sleek glossy black hair in a short bob, a soft white fringe of bangs framing her face, delicate gold hairpins shaped like snowflakes, sparkling diamond-blue eyes and a gentle blush. She wears an oversized white knit sweater with a cute little penguin knitted on the front, a flowing mid-length white pleated skirt that falls below her knees, a soft sky-blue scarf and white socks, with a thin gold necklace holding a small blue gem. She sits on a wide wooden window seat piled with pale blue and white cushions and a knitted blanket, holding a steaming white mug decorated with a little penguin in both hands and smiling warmly at the viewer. Through the large window behind her, a calm sky fades smoothly from soft gold near the horizon to icy pale blue and deep diamond blue at the top, over a glassy turquoise bay with glittering ice floes and drifting icebergs, where a few little penguins waddle along the snowy shore. A small hand-painted wooden sign on the windowsill reads \\"MOSS-Image\\". Cool winter light with warm gold accents, soft cel shading, clean line art, detailed background, gentle and wholesome mood."}}, "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": ["6", 0]}}, "8": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, bad anatomy, watermark, jpeg artifact", "clip": ["4", 0]}}, "9": {"class_type": "ComfyMathExpression", "inputs": {"expression": "1 if a == 0 else (1 if a == 1 else (1 if a == 2 else (1 if a == 3 else (1 if a == 4 else (0)))))", "values.a": ["1", 1]}}, "10": {"class_type": "ComfyMathExpression", "inputs": {"expression": "0 if a == 0 else (1 if a == 1 else (0 if a == 2 else (0 if a == 3 else (0 if a == 4 else (0)))))", "values.a": ["1", 1]}}, "11": {"class_type": "ComfyMathExpression", "inputs": {"expression": "0 if a == 0 else (0 if a == 1 else (0.28 if a == 2 else (0.35 if a == 3 else (0 if a == 4 else (0)))))", "values.a": ["1", 1]}}, "12": {"class_type": "ComfyMathExpression", "inputs": {"expression": "0 if a == 0 else (0 if a == 1 else (0 if a == 2 else (0 if a == 3 else (0.35 if a == 4 else (0)))))", "values.a": ["1", 1]}}, "13": {"class_type": "ComfyMathExpression", "inputs": {"expression": "25 if a == 0 else (25 if a == 1 else (14 if a == 2 else (10 if a == 3 else (10 if a == 4 else (25)))))", "values.a": ["1", 1]}}, "14": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "moss_image_sft_banding_fix_L37-41_bf16.safetensors", "model": ["3", 0], "strength_model": ["9", 0]}}, "15": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "moss_image_sft_banding_fix_L32-36_extra_bf16.safetensors", "model": ["14", 0], "strength_model": ["10", 0]}}, "16": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "lodestones_moss_sft_8step_dmd_lora_latest.safetensors", "model": ["15", 0], "strength_model": ["11", 0]}}, "17": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "darask0_moss_sft_4step_dmad_lora.safetensors", "model": ["16", 0], "strength_model": ["12", 0]}}, "18": {"class_type": "TemporalScoreRescaling", "inputs": {"tsr_k": 0.95, "tsr_sigma": 1.0, "model": ["17", 0]}}, "19": {"class_type": "SkipLayerGuidanceDiTSimple", "inputs": {"double_layers": "", "single_layers": "22, 24", "start_percent": 0.1, "end_percent": 0.7, "model": ["18", 0]}}, "20": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "denoise": 1.0, "model": ["19", 0], "steps": ["13", 1]}}, "21": {"class_type": "SamplerER_SDE", "inputs": {"solver_type": "ER-SDE", "max_stage": 3, "eta": 1.0, "s_noise": 1.05}}, "22": {"class_type": "PrimitiveInt", "inputs": {"value": 7}}, "23": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}}, "24": {"class_type": "SamplerCustom", "inputs": {"add_noise": true, "cfg": 3.0, "model": ["19", 0], "positive": ["7", 0], "negative": ["8", 0], "sampler": ["21", 0], "sigmas": ["20", 0], "latent_image": ["23", 0], "noise_seed": ["22", 0]}}, "25": {"class_type": "VAEDecode", "inputs": {"samples": ["24", 0], "vae": ["5", 0]}}, "26": {"class_type": "SaveImage", "inputs": {"filename_prefix": "moss_banding_fix", "images": ["25", 0]}}}'
_TEMPLATE_PROMPT = json.loads(_TEMPLATE_JSON)


def resolve_preset(preset_str: str | None) -> tuple[str, int]:
    if not preset_str:
        return PRESETS[4], 4  # Fast alt default
    s = preset_str.strip().lower()
    for idx, p in enumerate(PRESETS):
        if p.lower() == s:
            return p, idx
    if "fast alt" in s or "fast-alt" in s or "darask" in s:
        return PRESETS[4], 4
    if "fast" in s or "dmd" in s:
        return PRESETS[3], 3
    if "balanced" in s:
        return PRESETS[2], 2
    if "quality+" in s:
        return PRESETS[1], 1
    if "quality" in s or "fix" in s:
        return PRESETS[0], 0
    if "off" in s or "plain" in s:
        return PRESETS[5], 5
    return PRESETS[4], 4


def _resolve_seed(seed: int | None) -> int:
    return seed if (isinstance(seed, int) and seed >= 0) else random.randint(1, 2**32 - 1)


def make_moss_txt2img_graph(
    prompt: str,
    negative_prompt: str = "worst quality, bad anatomy, watermark, jpeg artifact",
    preset: str = "Fast alt · fix + darask0 0.35 · 10 steps",
    seed: int = -1,
    width: int = 1024,
    height: int = 1024,
) -> dict:
    graph = copy.deepcopy(_TEMPLATE_PROMPT)
    choice, idx = resolve_preset(preset)

    graph["1"]["inputs"]["choice"] = choice
    graph["1"]["inputs"]["index"] = idx
    graph["6"]["inputs"]["value"] = prompt
    graph["8"]["inputs"]["text"] = negative_prompt or "worst quality, bad anatomy, watermark, jpeg artifact"
    graph["22"]["inputs"]["value"] = _resolve_seed(seed)
    graph["23"]["inputs"]["width"] = max(256, min(2048, int(width or 1024)))
    graph["23"]["inputs"]["height"] = max(256, min(2048, int(height or 1024)))

    return graph


def make_moss_img2img_graph(
    prompt: str,
    image: str,
    negative_prompt: str = "worst quality, bad anatomy, watermark, jpeg artifact",
    preset: str = "Fast alt · fix + darask0 0.35 · 10 steps",
    denoise: float = 0.75,
    seed: int = -1,
) -> dict:
    """Build a MOSS-Image DeBand Img2Img graph using an uploaded reference image filename."""
    graph = copy.deepcopy(_TEMPLATE_PROMPT)
    choice, idx = resolve_preset(preset)

    graph["1"]["inputs"]["choice"] = choice
    graph["1"]["inputs"]["index"] = idx
    graph["6"]["inputs"]["value"] = prompt
    graph["8"]["inputs"]["text"] = negative_prompt or "worst quality, bad anatomy, watermark, jpeg artifact"
    graph["20"]["inputs"]["denoise"] = max(0.05, min(1.0, float(denoise if denoise is not None else 0.75)))
    graph["22"]["inputs"]["value"] = _resolve_seed(seed)

    # Replace EmptyFlux2LatentImage ("23") with LoadImage ("30") -> VAEEncode ("23")
    graph["30"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image, "upload": "image"},
    }
    graph["23"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["30", 0], "vae": ["5", 0]},
    }
    graph["26"]["inputs"]["filename_prefix"] = "moss_img2img"
    return graph


def make_sd35_txt2img_graph(
    prompt: str,
    negative_prompt: str = "worst quality, low quality, blurry, deformed",
    seed: int = -1,
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
) -> dict:
    """Official SD 3.5 Large fp8 text-to-image workflow graph."""
    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd3.5_large_fp8_scaled.safetensors"},
        },
        "16": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["4", 1], "text": prompt},
        },
        "40": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["4", 1], "text": negative_prompt or ""},
        },
        "53": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": max(256, min(2048, int(width or 1024))),
                "height": max(256, min(2048, int(height or 1024))),
                "batch_size": 1,
            },
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": _resolve_seed(seed),
                "steps": int(steps or 20),
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "sgm_uniform",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["16", 0],
                "negative": ["40", 0],
                "latent_image": ["53", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "sd35_large", "images": ["8", 0]},
        },
    }


def make_sd35_img2img_graph(
    prompt: str,
    image: str,
    negative_prompt: str = "worst quality, low quality, blurry, deformed",
    denoise: float = 0.75,
    seed: int = -1,
    steps: int = 20,
) -> dict:
    """Official SD 3.5 Large fp8 image-to-image workflow graph."""
    graph = make_sd35_txt2img_graph(prompt=prompt, negative_prompt=negative_prompt, seed=seed, steps=steps)
    graph["54"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image, "upload": "image"},
    }
    graph["53"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["54", 0], "vae": ["4", 2]},
    }
    graph["3"]["inputs"]["denoise"] = max(0.05, min(1.0, float(denoise if denoise is not None else 0.75)))
    graph["9"]["inputs"]["filename_prefix"] = "sd35_img2img"
    return graph


def make_flux_klein_txt2img_graph(
    prompt: str,
    negative_prompt: str = "",
    preset: str = "Fast",
    seed: int = -1,
    width: int = 1024,
    height: int = 1024,
) -> dict:
    """Official FLUX.2 Klein 4B fp8 text-to-image workflow graph (4-step Distilled default or 20-step Base)."""
    p_low = (preset or "").strip().lower()
    is_base = any(k in p_low for k in ("quality", "base", "20"))
    unet_name = "flux-2-klein-base-4b-fp8.safetensors" if is_base else "flux-2-klein-4b-fp8.safetensors"
    steps = 20 if is_base else 4
    cfg = 5.0 if is_base else 1.0
    w = max(256, min(2048, int(width or 1024)))
    h = max(256, min(2048, int(height or 1024)))

    graph = {
        "70": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "71": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2", "device": "default"},
        },
        "72": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "flux2-vae.safetensors"},
        },
        "74": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["71", 0], "text": prompt},
        },
        "66": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        },
        "61": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "62": {
            "class_type": "Flux2Scheduler",
            "inputs": {"steps": steps, "width": w, "height": h},
        },
        "63": {
            "class_type": "CFGGuider",
            "inputs": {"model": ["70", 0], "positive": ["74", 0], "negative": ["76", 0], "cfg": cfg},
        },
        "73": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": _resolve_seed(seed)},
        },
        "64": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["73", 0],
                "guider": ["63", 0],
                "sampler": ["61", 0],
                "sigmas": ["62", 0],
                "latent_image": ["66", 0],
            },
        },
        "65": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["64", 0], "vae": ["72", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "Flux2-Klein", "images": ["65", 0]},
        },
    }
    if is_base:
        graph["76"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["71", 0], "text": negative_prompt or ""},
        }
    else:
        graph["76"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["74", 0]},
        }
    return graph


def make_flux_klein_img2img_graph(
    prompt: str,
    image: str,
    negative_prompt: str = "",
    preset: str = "Fast",
    seed: int = -1,
) -> dict:
    """Official FLUX.2 Klein 4B fp8 ReferenceLatent image editing workflow graph."""
    p_low = (preset or "").strip().lower()
    is_base = any(k in p_low for k in ("quality", "base", "20"))
    unet_name = "flux-2-klein-base-4b-fp8.safetensors" if is_base else "flux-2-klein-4b-fp8.safetensors"
    steps = 20 if is_base else 4
    cfg = 5.0 if is_base else 1.0

    graph = {
        "70": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "71": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "flux2", "device": "default"},
        },
        "72": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "flux2-vae.safetensors"},
        },
        "76": {
            "class_type": "LoadImage",
            "inputs": {"image": image, "upload": "image"},
        },
        "80": {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {"image": ["76", 0], "upscale_method": "nearest-exact", "megapixels": 1.0, "resolution_steps": 1},
        },
        "99": {
            "class_type": "GetImageSize",
            "inputs": {"image": ["80", 0]},
        },
        "66": {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": ["99", 0], "height": ["99", 1], "batch_size": 1},
        },
        "74": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["71", 0], "text": prompt},
        },
        "122": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["80", 0], "vae": ["72", 0]},
        },
        "123": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["74", 0], "latent": ["122", 0]},
        },
        "121": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["82", 0], "latent": ["122", 0]},
        },
        "61": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "62": {
            "class_type": "Flux2Scheduler",
            "inputs": {"steps": steps, "width": ["99", 0], "height": ["99", 1]},
        },
        "63": {
            "class_type": "CFGGuider",
            "inputs": {"model": ["70", 0], "positive": ["123", 0], "negative": ["121", 0], "cfg": cfg},
        },
        "73": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": _resolve_seed(seed)},
        },
        "64": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["73", 0],
                "guider": ["63", 0],
                "sampler": ["61", 0],
                "sigmas": ["62", 0],
                "latent_image": ["66", 0],
            },
        },
        "65": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["64", 0], "vae": ["72", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "Flux2-Klein-Edit", "images": ["65", 0]},
        },
    }
    if is_base:
        graph["82"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["71", 0], "text": negative_prompt or ""},
        }
    else:
        graph["82"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["74", 0]},
        }
    return graph


DEFAULT_WAN_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
    "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，"
    "手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)


def make_wan_video_graph(
    prompt: str,
    image: str | None = None,
    negative_prompt: str = "",
    preset: str = "Quality",
    seed: int = -1,
    width: int = 832,
    height: int = 480,
    length: int = 33,
) -> dict:
    """Official Wan 2.2 5B TI2V / Wan 2.1 1.3B T2V video workflow graph."""
    w = 832 if (width == 1024 and height == 1024) else max(256, min(1280, int(width or 832)))
    h = 480 if (width == 1024 and height == 1024) else max(256, min(1280, int(height or 480)))
    neg = negative_prompt if (negative_prompt and "watermark, jpeg artifact" not in negative_prompt) else DEFAULT_WAN_NEGATIVE
    p_low = (preset or "").strip().lower()
    use_13b = (not image) and any(k in p_low for k in ("1.3b", "wan21"))

    if use_13b:
        return {
            "37": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": "wan2.1_t2v_1.3B_fp16.safetensors", "weight_dtype": "default"},
            },
            "38": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"},
            },
            "39": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "wan_2.1_vae.safetensors"},
            },
            "48": {
                "class_type": "ModelSamplingSD3",
                "inputs": {"model": ["37", 0], "shift": 8.0},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["38", 0], "text": prompt},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["38", 0], "text": neg},
            },
            "40": {
                "class_type": "EmptyHunyuanLatentVideo",
                "inputs": {"width": w, "height": h, "length": int(length or 33), "batch_size": 1},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["48", 0],
                    "seed": _resolve_seed(seed),
                    "steps": 20,
                    "cfg": 6.0,
                    "sampler_name": "uni_pc",
                    "scheduler": "simple",
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["40", 0],
                    "denoise": 1.0,
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
            },
            "49": {
                "class_type": "CreateVideo",
                "inputs": {"images": ["8", 0], "fps": 16.0},
            },
            "50": {
                "class_type": "SaveVideo",
                "inputs": {"video": ["49", 0], "filename_prefix": "video/Wan2.1", "format": "auto", "codec": "auto"},
            },
        }

    latent_inputs: dict = {"vae": ["39", 0], "width": w, "height": h, "length": int(length or 33), "batch_size": 1}
    graph = {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"},
        },
        "38": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"},
        },
        "39": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "wan2.2_vae.safetensors"},
        },
        "48": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["37", 0], "shift": 8.0},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["38", 0], "text": prompt},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["38", 0], "text": neg},
        },
        "55": {
            "class_type": "Wan22ImageToVideoLatent",
            "inputs": latent_inputs,
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["48", 0],
                "seed": _resolve_seed(seed),
                "steps": 20,
                "cfg": 5.0,
                "sampler_name": "uni_pc",
                "scheduler": "simple",
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["55", 0],
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
        },
        "57": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["8", 0], "fps": 24.0},
        },
        "58": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["57", 0], "filename_prefix": "video/Wan2.2", "format": "auto", "codec": "auto"},
        },
    }
    if image:
        graph["56"] = {
            "class_type": "LoadImage",
            "inputs": {"image": image, "upload": "image"},
        }
        latent_inputs["start_image"] = ["56", 0]
    return graph


def make_txt2img_graph(
    profile: str | None,
    prompt: str,
    negative_prompt: str = "worst quality, bad anatomy, watermark, jpeg artifact",
    preset: str = "Fast alt · fix + darask0 0.35 · 10 steps",
    seed: int = -1,
    width: int = 1024,
    height: int = 1024,
) -> dict:
    """Build a Txt2Img / Txt2Vid prompt graph matching the active profile."""
    if profile == "flux-klein":
        return make_flux_klein_txt2img_graph(
            prompt=prompt,
            negative_prompt=negative_prompt,
            preset=preset,
            seed=seed,
            width=width,
            height=height,
        )
    if profile == "wan-ltx-video":
        return make_wan_video_graph(
            prompt=prompt,
            negative_prompt=negative_prompt,
            preset=preset,
            seed=seed,
            width=width,
            height=height,
        )
    if profile == "sd-latest":
        return make_sd35_txt2img_graph(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
        )
    return make_moss_txt2img_graph(
        prompt=prompt,
        negative_prompt=negative_prompt,
        preset=preset,
        seed=seed,
        width=width,
        height=height,
    )


def make_img2img_graph(
    profile: str | None,
    prompt: str,
    image: str,
    negative_prompt: str = "worst quality, bad anatomy, watermark, jpeg artifact",
    preset: str = "Fast alt · fix + darask0 0.35 · 10 steps",
    denoise: float = 0.75,
    seed: int = -1,
) -> dict:
    """Build an Img2Img / Img2Vid prompt graph matching the active profile."""
    if profile == "flux-klein":
        return make_flux_klein_img2img_graph(
            prompt=prompt,
            image=image,
            negative_prompt=negative_prompt,
            preset=preset,
            seed=seed,
        )
    if profile == "wan-ltx-video":
        return make_wan_video_graph(
            prompt=prompt,
            image=image,
            negative_prompt=negative_prompt,
            preset=preset,
            seed=seed,
        )
    if profile == "sd-latest":
        return make_sd35_img2img_graph(
            prompt=prompt,
            image=image,
            negative_prompt=negative_prompt,
            denoise=denoise,
            seed=seed,
        )
    return make_moss_img2img_graph(
        prompt=prompt,
        image=image,
        negative_prompt=negative_prompt,
        preset=preset,
        denoise=denoise,
        seed=seed,
    )
