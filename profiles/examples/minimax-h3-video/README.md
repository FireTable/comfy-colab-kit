# Profile: `minimax-h3-video`

MiniMax-H3 **TriPRISM TurboFusion** omni-modal video + native stereo audio generation on Colab **A100-80G** (`high_mem: true`, primary), with automatic resilient fallback to **G4 (96 GB)** and **L4 (24 GB)** (`A100 → G4 → L4`).

The default workflow is **`h3-r2v` (8-step Ref2V + native stereo audio)**; **`h3-i2v`**, **`h3-extend`**, **`h3-controlnet`**, and optional **`h3-t2v`** are included.

- **TriPRISM 8-Step Baked Backbone**:
  - Uses `OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion` (`Sulphur-3` + `10Eros-Max` + `Singularity V2` with LightX2V TurboFusion 8-step & Realism Slider baked in).
  - **A100-80G / G4-96G**: Runs full 50-block `int8_convrot` (`19.61 GB`) alongside `Qwen3-VL-32B NVFP4` (`14.61 GB`), `Video VAE INT8` (`2.62 GB`), and `Audio VAE FP32` (`0.56 GB`) — **~37.40 GB core wake download**.
  - **L4-24G Fallback (`model_aliases_by_gpu.L4`)**: Automatically swaps the DiT alias to the 42-block `h3tiny17b_int8_convrot` (`16.00 GB`) so 24 GB L4 instances run zero-OOM (**~33.79 GB core wake download**).
  - **Fast Feedback Defaults**: Official templates default to `0.4 MP` (`864×480` at `16:9` or `640×640` at `1:1`) and `overrides` lock sampling to **8 steps** with template Lightning LoRAs bypassed (`strength = 0`, no double-acceleration).
- **Long Clips & High-Consistency Upscaling (`h3-extend` + `ComfyUI_MiniMax_H3_Extender`)**:
  - `h3-extend` ships the official `video_minimax_h3_i2v_continuation.json` template in App Mode and installs [`tritant/ComfyUI_MiniMax_H3_Extender`](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender) (Apache-2.0) for Graph Mode multi-clip Motion Context continuation, disk caching, built-in **Latent Refine/Upscale**, and **RelayVSR ×2** (`relayvsr-x2` + `relayvsr-vae`, fetched on demand).
- **White-Model Video-to-Video (`h3-controlnet`)**:
  - Uses `MiniMax-H3-Fun-Controlnet-Union` (`2.14 GB`) with built-in ComfyUI `SDPose` (`1.79 GB`) + `RT-DETR v4` (`0.12 GB`) pose/skeleton extraction (supports Canny, Depth, Pose, HED, MLSD, and video inpainting in Graph Mode; fetched on demand).
- **Workflows are never self-authored**: All templates are unmodified MIT templates from `Comfy-Org/workflow_templates`, vendored under [`workflows/`](workflows/).
- **Models are never vendored**: Every checkpoint is streamed from Hugging Face at wake/fetch time and SHA-256 verified.

## Workflows

| id — App menu title | Source | License | Shipped as | Role | Default set |
|---|---|---|---|---|---|
| `h3-r2v` — Ref2Video · MiniMax-H3 TriPRISM 8-Step R2V + Stereo Audio | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 community license | vendored `workflows/video_minimax_h3_r2v.json` | Multi-ref image/video/voice R2V | ✅ default |
| `h3-i2v` — I2V · MiniMax-H3 TriPRISM 8-Step Image-to-Video | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 community license | vendored `workflows/video_minimax_h3_i2v.json` | First/last-frame I2V | ✅ |
| `h3-extend` — Long · MiniMax-H3 Continuation & Extender + RelayVSR x2 | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template + Apache-2.0 Extender | vendored `workflows/video_minimax_h3_i2v_continuation.json` | Continuation + Graph Mode Extender & RelayVSR ×2 | ✅ |
| `h3-controlnet` — V2V · MiniMax-H3 Fun ControlNet-Union Pose/Depth/Canny | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 + MIT SDPose | vendored `workflows/video_minimax_h3_fun_controlnet_union.json` | Pose/Depth/Canny V2V (on-demand weights) | ✅ |
| `h3-t2v` — T2V · MiniMax-H3 TriPRISM 8-Step Text-to-Video | [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | MIT template; MiniMax H3 community license | vendored `workflows/video_minimax_h3_t2v.json` | Pure text-to-video | optional |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `dit-triprism-int8` | `diffusion_models/Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_int8_convrot.safetensors` | [OliviaRossi/TriPrism-TurboFusion](https://huggingface.co/OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion) | minimax-h3-community-license-agreement | 19.61 | core (A100/G4) |
| `dit-triprism-tiny` | `diffusion_models/Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_h3tiny17b_int8_convrot.safetensors` | [OliviaRossi/TriPrism-TurboFusion](https://huggingface.co/OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion) | minimax-h3-community-license-agreement | 16.00 | L4 fallback alias |
| `dit-triprism-w6a8` | `diffusion_models/Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_pruned_w6a8_g32.safetensors` | [OliviaRossi/TriPrism-TurboFusion](https://huggingface.co/OliviaRossi/Minimax-H3-Sulphur3-10Erosmax-SingularityV2-TriPrism-TurboFusion) | minimax-h3-community-license-agreement | 14.97 | optional |
| `q3vl-nvfp4` | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 14.61 | core |
| `video-vae-int8` | `vae/minimax_h3_video_vae_int8_convrot.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 2.62 | core |
| `audio-vae` | `vae/minimax_h3_audio_vae_fp32.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 0.56 | core |
| `fun-controlnet-union` | `model_patches/minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) | minimax-h3-community-license-agreement | 2.14 | on-demand (`h3-controlnet`) |
| `sdpose-wholebody` | `checkpoints/sdpose_wholebody_fp16.safetensors` | [Comfy-Org/SDPose](https://huggingface.co/Comfy-Org/SDPose) | mit | 1.79 | on-demand (`h3-controlnet`) |
| `rtdetr-hgnet` | `diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors` | [Comfy-Org/SDPose](https://huggingface.co/Comfy-Org/SDPose) | apache-2.0 | 0.12 | on-demand (`h3-controlnet`) |
| `relayvsr-x2` | `diffusion_models/relayvsr_x2_lightx2v_int8_convrot.safetensors` | [Comfy-Org/RelayVSR](https://huggingface.co/Comfy-Org/RelayVSR) | apache-2.0 | 4.81 | on-demand (`h3-extend`) |
| `relayvsr-vae` | `vae/relayvsr_flash_vae_x2_fp32.safetensors` | [Comfy-Org/RelayVSR](https://huggingface.co/Comfy-Org/RelayVSR) | apache-2.0 | 0.12 | on-demand (`h3-extend`) |
| `h3-latent-upscaler` | `latent_upscale_models/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors` | [LBH-123-AI/Minimax_h3_latent_Upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler) | apache-2.0 | 0.64 | on-demand (`h3-extend`) |
