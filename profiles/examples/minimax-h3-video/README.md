# Profile: `minimax-h3-video`

MiniMax-H3 **TriPRISM TurboFusion** omni-modal video + native stereo audio generation on Colab **A100-80G** (`high_mem: true`, primary), with automatic resilient fallback to **G4 (96 GB)** and **L4 (24 GB)** (`A100 → G4 → L4`).

The default workflow is **`h3-r2v` (8-step Ref2V + native stereo audio)**; **`h3-i2v`**, **`h3-extend`**, **`h3-extender-latent`**, **`h3-controlnet`**, and optional **`h3-t2v`** are included.

- **TriPRISM 8-Step Baked Backbone**:
  - Uses `OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion` (`Sulphur-3` + `10Eros-Max` + `Singularity V2` with LightX2V TurboFusion 8-step & Realism Slider baked in).
  - **A100-80G / G4-96G**: Runs full 50-block `int8_convrot` (`19.61 GB`) alongside `Qwen3-VL-32B NVFP4` (`14.61 GB`), **`Video VAE FP16`** (`4.85 GB`, eliminates INT8 decode softness and multi-clip luminance darkening), and `Audio VAE FP32` (`0.56 GB`) — **~39.63 GB core wake download**.
  - **L4-24G Fallback (`model_aliases_by_gpu.L4`)**: Automatically swaps the DiT alias to the full 50-block `dit-triprism-w6a8` (`14.97 GB`) and the Video VAE alias to `video-vae-int8` (`2.62 GB`) so 24 GB L4 instances run zero-OOM (**~32.76 GB core wake download**) while preserving exact 50-block alignment with `MiniMaxH3FunControl` skip blocks `(0, 10, 20, 30, 40)` (the 42-block `dit-triprism-tiny` remains available as an optional model).
  - **Pure 2D Anime / Zero-RealismSlider Options**: Optional non-core 50-block models `dit-official-ref2va-w6a8` (`14.89 GB`, pair with `lora-ref2v-turbo-4step` `1.82 GB`) and uncensored `dit-sulphur3-w4a8` (`10.65 GB`) are declared for on-demand selection without inflating wake downloads.
  - **Per-GPU Resolution Defaults (`overrides_by_gpu`)**: Automatically scales default `megapixels` by allocated GPU tier (`0.4 MP` on `L4` for fast feedback, `0.8 MP` / `1216×672` on `A100`, `0.9 MP` / `1280×736` on `G4`/`H100`), while `overrides` lock sampling to **8 steps** with template Lightning LoRAs bypassed (`strength = 0`, no double-acceleration).
- **Long Clips & Anti-Darkening Continuation (`h3-extend` & `h3-extender-latent`)**:
  - `h3-extender-latent` (`minimax_h3_extender_latent.json`, Apache-2.0) chains multi-clip Motion Contexts **purely in latent/disk-cache space** via [`tritant/ComfyUI_MiniMax_H3_Extender`](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender), avoiding MP4 YUV420 ↔ RGB round-trip luminance darkening.
  - `h3-extend` (`video_minimax_h3_i2v_continuation.json`) pairs `Video VAE FP16` with an install-time **`ColorTransfer`** (`reinhard_lab`, `uniform`, `strength=0.85`) anchor injected inside the subgraph between `VAEDecode` and `CreateVideo` (locked to `first_frame`) plus on-demand **RelayVSR ×2** (`relayvsr-x2` + `relayvsr-vae`) and `MiniMaxH3LatentUpscaleWithModel` (`h3-latent-upscaler`).
- **White-Model Video-to-Video (`h3-controlnet`)**:
  - Uses **MiniMax-H3-Fun-Controlnet-Union 2.0** (`4.22 GB`, mapped via `model_aliases` onto the unmodified MIT template) with built-in ComfyUI `SDPose` (`1.79 GB`) + `RT-DETR v4` (`0.12 GB`) pose/skeleton extraction (supports Canny, Depth, Pose, HED, MLSD, and video inpainting in Graph Mode; fetched on demand).
  - **Tip for monochrome/clay white-model videos**: `SDPose` tracks standard standing frames well but can miss extreme close-ups or splits on textureless clay renders; swap or add a built-in Depth or Canny preprocessor in Graph Mode for tighter structural lock.
- **Workflows are never self-authored**: All templates are unmodified MIT templates from `Comfy-Org/workflow_templates`, vendored under [`workflows/`](workflows/).
- **Models are never vendored**: Every checkpoint is streamed from Hugging Face at wake/fetch time and SHA-256 verified.

## Workflows

| id — App menu title | Source | License | Shipped as | Role | Default set |
|---|---|---|---|---|---|
| `h3-r2v` — Ref2Video · MiniMax-H3 TriPRISM 8-Step R2V + Stereo Audio | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 community license | vendored `workflows/video_minimax_h3_r2v.json` | Multi-ref image/video/voice R2V | ✅ default |
| `h3-i2v` — I2V · MiniMax-H3 TriPRISM 8-Step Image-to-Video | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 community license | vendored `workflows/video_minimax_h3_i2v.json` | First/last-frame I2V | ✅ |
| `h3-extend` — Long · MiniMax-H3 Continuation & Extender + RelayVSR x2 | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template + Apache-2.0 Extender | vendored `workflows/video_minimax_h3_i2v_continuation.json` | Continuation w/ `ColorTransfer` anchor + RelayVSR ×2 | ✅ |
| `h3-extender-latent` — Long · MiniMax-H3 Latent Motion-Context Extender | [tritant/ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender) | Apache-2.0 template & node; MiniMax H3 license | vendored `workflows/minimax_h3_extender_latent.json` | Zero-MP4-drift pure latent Motion Context chaining | ✅ |
| `h3-controlnet` — V2V · MiniMax-H3 Fun ControlNet-Union Pose/Depth/Canny | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 + MIT SDPose | vendored `workflows/video_minimax_h3_fun_controlnet_union.json` | Pose/Depth/Canny V2V (on-demand weights) | ✅ |
| `h3-t2v` — T2V · MiniMax-H3 TriPRISM 8-Step Text-to-Video | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 community license | vendored `workflows/video_minimax_h3_t2v.json` | Pure text-to-video | optional |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `dit-triprism-int8` | `diffusion_models/Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_int8_convrot.safetensors` | [OliviaRossi/TriPrism-TurboFusion](https://huggingface.co/OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion) | minimax-h3-community-license-agreement | 19.61 | core (A100/G4) |
| `dit-triprism-tiny` | `diffusion_models/Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_h3tiny17b_int8_convrot.safetensors` | [OliviaRossi/TriPrism-TurboFusion](https://huggingface.co/OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion) | minimax-h3-community-license-agreement | 16.00 | optional (42-block) |
| `dit-triprism-w6a8` | `diffusion_models/Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_pruned_w6a8_g32.safetensors` | [OliviaRossi/TriPrism-TurboFusion](https://huggingface.co/OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion) | minimax-h3-community-license-agreement | 14.97 | L4 fallback alias (50-block) |
| `dit-official-ref2va-w6a8` | `diffusion_models/minimax_h3_ref2va_pruned_w6a8.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 14.89 | optional (50-block, zero RealismSlider) |
| `lora-ref2v-turbo-4step` | `loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 1.82 | optional (4-step Ref2V LoRA) |
| `dit-sulphur3-w4a8` | `diffusion_models/s3_w4a8.safetensors` | [SulphurAI/Sulphur-3](https://huggingface.co/SulphurAI/Sulphur-3) | minimax-h3-community-license-agreement | 10.65 | optional (50-block uncensored, zero RealismSlider) |
| `q3vl-nvfp4` | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 14.61 | core |
| `video-vae-fp16` | `vae/minimax_h3_video_vae_fp16.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 4.85 | core (A100/G4) |
| `video-vae-int8` | `vae/minimax_h3_video_vae_int8_convrot.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 2.62 | L4 fallback alias |
| `audio-vae` | `vae/minimax_h3_audio_vae_fp32.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 0.56 | core |
| `fun-controlnet-union` | `model_patches/minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 4.22 | on-demand (`h3-controlnet`, 2.0) |
| `sdpose-wholebody` | `checkpoints/sdpose_wholebody_fp16.safetensors` | [Comfy-Org/SDPose](https://huggingface.co/Comfy-Org/SDPose) | mit | 1.79 | on-demand (`h3-controlnet`) |
| `rtdetr-hgnet` | `diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors` | [Comfy-Org/SDPose](https://huggingface.co/Comfy-Org/SDPose) | apache-2.0 | 0.12 | on-demand (`h3-controlnet`) |
| `relayvsr-x2` | `diffusion_models/relayvsr_x2_lightx2v_int8_convrot.safetensors` | [Comfy-Org/RelayVSR](https://huggingface.co/Comfy-Org/RelayVSR) | apache-2.0 | 4.81 | on-demand (`h3-extend`) |
| `relayvsr-vae` | `vae/relayvsr_flash_vae_x2_fp32.safetensors` | [Comfy-Org/RelayVSR](https://huggingface.co/Comfy-Org/RelayVSR) | apache-2.0 | 0.12 | on-demand (`h3-extend`) |
| `h3-latent-upscaler` | `latent_upscale_models/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors` | [LBH-123-AI/Minimax_h3_latent_Upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler) | apache-2.0 | 0.64 | on-demand (`h3-extend`) |
