# Profile: `wan-ltx-video`

**Wan 2.2 5B TI2V** (Text-and-Image-to-Video), **Wan 2.1 1.3B T2V**, and **Lightricks LTX-Video 2B** on a Google Colab L4 GPU (24 GB VRAM).

- **Workflows are never self-authored.** Every entry in [`workflows/`](workflows/) is an unmodified official template from [`Comfy-Org/workflow_templates`](https://github.com/Comfy-Org/workflow_templates) (MIT license), verified against `upstream_sha256` by `tests/test_profiles.py`.
- **Models are never vendored.** Weights are downloaded directly at runtime from official [Comfy-Org](https://huggingface.co/Comfy-Org) and [Lightricks](https://huggingface.co/Lightricks) Hugging Face repositories into RAM-backed `tmpfs`.
- The default **Wan 2.2 5B TI2V** workflow (`wan22-ti2v-5b`) requires **16.89 GB** (`wan2.2_ti2v_5B_fp16` + `umt5_xxl_fp8` + `wan2.2_vae`), while **Wan 2.1 1.3B T2V** (`wan21-t2v-1-3b`) shares the `umt5_xxl_fp8` text encoder and adds only **2.88 GB** in the background (`prefetch: true`), so both T2V and I2V are ready out of the box within 24 GB VRAM.

## Workflows

| id — App menu title | Source | License | Shipped as | Default set |
|---|---|---|---|---|
| `wan22-ti2v-5b` — Quality · Wan 2.2 5B Text/Image-to-Video (official) | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | MIT template; weights Apache-2.0 (Alibaba Wan Team) | vendored `workflows/official_video_wan2_2_5B_ti2v.json` | ✅ default |
| `wan21-t2v-1-3b` — Fast · Wan 2.1 1.3B Text-to-Video (official) | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged) | MIT template; weights Apache-2.0 (Alibaba Wan Team) | vendored `workflows/official_text_to_video_wan.json` | ✅ (`prefetch: true`) |
| `ltxv-2b-i2v` — Fast · LTX-Video 2B Image-to-Video (official) | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) | MIT template; weights OpenRail-M (Lightricks) | vendored `workflows/official_ltxv_image_to_video.json` | optional |
| `ltxv-2b-t2v` — Fast · LTX-Video 2B Text-to-Video (official) | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) | MIT template; weights OpenRail-M (Lightricks) | vendored `workflows/official_ltxv_text_to_video.json` | optional |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `wan22-ti2v-5b` | `diffusion_models/wan2.2_ti2v_5B_fp16.safetensors` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | Apache-2.0 | 9.31 | core |
| `umt5-xxl-fp8` | `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged) | Apache-2.0 | 6.27 | core |
| `wan22-vae` | `vae/wan2.2_vae.safetensors` | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | Apache-2.0 | 1.31 | core |
| `wan21-t2v-1-3b` | `diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors` | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged) | Apache-2.0 | 2.64 | prefetch |
| `wan21-vae` | `vae/wan_2.1_vae.safetensors` | [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged) | Apache-2.0 | 0.24 | prefetch |
| `ltxv-2b` | `checkpoints/ltx-video-2b-v0.9.5.safetensors` | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) | OpenRail-M | 5.91 | optional |
| `t5-fp8` | `text_encoders/t5xxl_fp8_e4m3fn.safetensors` | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | Apache-2.0 | 4.89 | optional |

## Credits & Acknowledgments

- **[Alibaba Wan Team (Tongyi Lab)](https://github.com/Wan-Video/Wan2.2)**: Creators of the Apache-2.0 Wan 2.2 5B TI2V and Wan 2.1 1.3B open video generation models.
- **[Lightricks](https://github.com/Lightricks/LTX-Video)**: Creators of the LTX-Video 2B real-time video diffusion model ([`Lightricks/LTX-Video`](https://huggingface.co/Lightricks/LTX-Video)).
- **[Comfy-Org](https://github.com/Comfy-Org)**: Authors of the official MIT workflow templates ([`Comfy-Org/workflow_templates`](https://github.com/Comfy-Org/workflow_templates)) and ComfyUI repackaged weights ([`Comfy-Org/Wan_2.2_ComfyUI_Repackaged`](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged), [`Comfy-Org/Wan_2.1_ComfyUI_repackaged`](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged)).
