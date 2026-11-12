# Profile: `minimax-h3-image`

MiniMax-H3-Image (Comfy-Org official templates & quantized models) on a Colab L4 (24 GB VRAM).
The default workflow is **`h3-t2i` (6-step T2I)**; the multi-reference image editing workflow **`h3-edit`** is also included.

- **Best Implementation for 24 GB L4**:
  - **DiT Backbone**: Uses `minimax_h3_image_pruned_w4a8.safetensors` (~11.69 GB), aliased automatically to replace `pruned_int8` at install time. This leaves 10+ GB VRAM for attention activation, guaranteeing zero CUDA OOM on Colab L4 during 2K~4K 6-step sampling.
  - **Text & Multimodal Encoder**: `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` (25.28 GB) loaded via ComfyUI with High-RAM system memory offload. Encoding runs once in ~3–5 seconds with 100% semantic fidelity, then offloads to make room for the DiT.
  - **Prompt Enhancer**: Optional `qwen3.5_9b_w4a8_gptq.safetensors` (6.84 GB) with `TextGenerateV2` for automatic cinematic prompt expansion.
  - **VAE**: `minimax_h3_image_vae_fp16.safetensors` (1.09 GB).
- **Workflows are never self-authored**: Each entry is an unmodified upstream official Comfy-Org template (MIT license), vendored under [`workflows/`](workflows/).
- **Models are never vendored**: Every model is downloaded from `Comfy-Org/MiniMax-H3-Image` on Hugging Face; SHA-256 values are verified at boot time.
- **App Mode (Linear Mode)**: Fully decorated with bilingual input controls (positive prompt, prompt enhancer toggle, dimensions, image upload) and output image preview.

Source of truth: Comfy-Org MiniMax-H3-Image release (2026-11), mirrored in [`profile.yaml`](profile.yaml).

## Workflows

| id — App menu title | Source | License | Shipped as | Tier | Default set |
|---|---|---|---|---|---|
| `h3-t2i` — Quality · MiniMax-H3-Image T2I (official) | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | MIT template; weights MiniMax H3 Image community license | vendored `workflows/official_minimax_h3_image_t2i.json` | TIER1 quality (6 steps, CFG 1) | ✅ default |
| `h3-edit` — Edit · MiniMax-H3-Image Multi-Ref Edit (official) | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | MIT template; weights MiniMax H3 Image community license | vendored `workflows/official_minimax_h3_image_edit.json` | TIER1 multi-reference edit | ✅ |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `dit-w4a8` | `diffusion_models/minimax_h3_image_pruned_w4a8.safetensors` | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | minimax-h3-image-community-license-agreement | 11.69 | core |
| `dit-int8` | `diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors` | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | minimax-h3-image-community-license-agreement | 19.54 | optional |
| `q3vl-int8` | `text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | minimax-h3-image-community-license-agreement | 25.28 | core |
| `q3vl-nvfp4` | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | minimax-h3-image-community-license-agreement | 14.61 | optional |
| `pe-gptq` | `text_encoders/qwen3.5_9b_w4a8_gptq.safetensors` | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | apache-2.0 | 6.84 | core |
| `h3-vae` | `vae/minimax_h3_image_vae_fp16.safetensors` | [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image) | minimax-h3-image-community-license-agreement | 1.09 | core |

## ComfyUI settings in this profile

* **`--reserve-vram 1.5`**: Leaves 1.5 GB VRAM headroom for PyTorch allocation, preventing fragmentation crashes on L4.
* **`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`**: Critical for dynamic tensor resizing across 6 sampling steps.
