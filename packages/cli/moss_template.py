"""MOSS-Image DeBand API Workflow Template.

Pre-configured 25-node pipeline with CustomCombo Preset selection,
anti-banding LoRAs (L37-41 & L32-36), and fast sampling.
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
  "Off \u00b7 plain SFT (banding) \u00b7 25 steps"
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
    if 'fast alt' in s or 'fast-alt' in s or 'darask' in s:
        return PRESETS[4], 4
    if 'fast' in s or 'dmd' in s:
        return PRESETS[3], 3
    if 'balanced' in s:
        return PRESETS[2], 2
    if 'quality+' in s:
        return PRESETS[1], 1
    if 'quality' in s or 'fix' in s:
        return PRESETS[0], 0
    if 'off' in s or 'plain' in s:
        return PRESETS[5], 5
    return PRESETS[4], 4


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
    
    # 1. Preset choice & index
    graph["1"]["inputs"]["choice"] = choice
    graph["1"]["inputs"]["index"] = idx
    
    # 2. Prompts
    graph["6"]["inputs"]["value"] = prompt
    graph["8"]["inputs"]["text"] = negative_prompt or "worst quality, bad anatomy, watermark, jpeg artifact"
    
    # 3. Seed
    actual_seed = seed if (isinstance(seed, int) and seed >= 0) else random.randint(1, 2**32 - 1)
    graph["22"]["inputs"]["value"] = actual_seed
    
    # 4. Latent Size
    graph["23"]["inputs"]["width"] = max(256, min(2048, int(width or 1024)))
    graph["23"]["inputs"]["height"] = max(256, min(2048, int(height or 1024)))
    
    return graph
