# Profile: `sd-latest`

Stable Diffusion 3.5 on a Colab L4 (replaces the old SD 1.5 minimal profile). Default: the official
**SD 3.5 Large fp8** graph; fast variants use Tensor.Art's TurboX (Large, 8 steps) and Medium turbo LoRAs.

- **Workflows are never self-authored.** Each entry is an unmodified upstream file. It is *vendored* under
  [`workflows/`](workflows/) only when its license allows redistribution (Apache-2.0 or the MIT official
  templates); everything else is *URL-only* and fetched from the author at setup time. `upstream_sha256`
  pins the reviewed version (vendored files are checked by `tests/test_profiles.py`).
- **Models are never vendored.** Every model is downloaded from its original Hugging Face repo; SHA-256 values
  are the LFS object ids at review time.
- The kit only adds ComfyUI's App-mode metadata (`extra.linearData`, `extra.linearMode`) and, where declared
  under `overrides`, swaps a file name in a loader widget.

Source of truth: the reviewed workflow manifest (2026-10-23), mirrored in [`profile.yaml`](profile.yaml).

## Workflows

| id — App menu title | Source | License | Shipped as | Tier | Default set |
|---|---|---|---|---|---|
| `sd35-large` — Quality · SD 3.5 Large fp8 (official, 20 steps) | [ComfyUI_examples/sd3/](https://comfyanonymous.github.io/ComfyUI_examples/sd3/) | MIT; model Stability Community License (gated) | vendored `workflows/official_sd3.5_simple.json` | TIER1 quality baseline | ✅ default |
| `sd35l-turbox` — Fast · SD 3.5 Large TurboX 8-step (Tensor.Art) | [tensorart/stable-diffusion-3.5-large-TurboX](https://huggingface.co/tensorart/stable-diffusion-3.5-large-TurboX) | Stability Community License (other) | URL-only ([upstream](https://huggingface.co/tensorart/stable-diffusion-3.5-large-TurboX/resolve/main/contrast_sd3.5L_TurboX_turbo_diff_cfg.json)) | TIER1 speed | ✅ |
| `sd35m-4step` — Fastest · SD 3.5 Medium 4-step LoRA (Tensor.Art, gated base) | [tensorart/stable-diffusion-3.5-medium-turbo](https://huggingface.co/tensorart/stable-diffusion-3.5-medium-turbo) | Stability Community License | URL-only ([upstream](https://huggingface.co/tensorart/stable-diffusion-3.5-medium-turbo/resolve/main/sd3.5m-4steps-nonstop.json)) | TIER2 fastest small | optional |
| `sd35m-8step` — Fast · SD 3.5 Medium 8-step LoRA (Tensor.Art, gated base) | [tensorart/stable-diffusion-3.5-medium-turbo](https://huggingface.co/tensorart/stable-diffusion-3.5-medium-turbo) | Stability Community License | URL-only ([upstream](https://huggingface.co/tensorart/stable-diffusion-3.5-medium-turbo/resolve/main/contrast_lora_.json)) | TIER2 | optional |

Optional workflows are enabled per wake: `POST /_gate/wake {"profile": "sd-latest", "workflows": ["<id>"]}` or
`ckit wake --profile sd-latest --workflow <id>` (see [docs/AGENT_SKILL.md](../../../docs/AGENT_SKILL.md)).

### Considered and excluded

| Candidate | Reason |
|---|---|
| SD 1.5 / SDXL / Hyper-SD workflows | Superseded (`_superseded_sd15/`); `tests/fixtures/profiles/tiny` keeps an SD 1.5 fixture for tests only |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `sd35l-fp8` | `checkpoints/sd3.5_large_fp8_scaled.safetensors` | [Comfy-Org/stable-diffusion-3.5-fp8](https://huggingface.co/Comfy-Org/stable-diffusion-3.5-fp8) | Stability AI Community License | 14.93 | core |
| `sd35m` | `checkpoints/sd3.5_medium.safetensors` | [stabilityai/stable-diffusion-3.5-medium](https://huggingface.co/stabilityai/stable-diffusion-3.5-medium) | Stability AI Community License (gated: accept on HF, set HF_TOKEN_FILE) | 5.11 | gated |
| `clip-g` | `text_encoders/clip_g.safetensors` | [Comfy-Org/stable-diffusion-3.5-fp8](https://huggingface.co/Comfy-Org/stable-diffusion-3.5-fp8) | Stability AI Community License | 1.39 | |
| `clip-l` | `text_encoders/clip_l.safetensors` | [Comfy-Org/stable-diffusion-3.5-fp8](https://huggingface.co/Comfy-Org/stable-diffusion-3.5-fp8) | Stability AI Community License | 0.25 | |
| `t5-fp8` | `text_encoders/t5xxl_fp8_e4m3fn.safetensors` | [Comfy-Org/stable-diffusion-3.5-fp8](https://huggingface.co/Comfy-Org/stable-diffusion-3.5-fp8) | Stability AI Community License | 4.89 | |
| `turbox-8step` | `loras/Tensorart_TurboX_sd3.5L_8steps.safetensors` | [tensorart/stable-diffusion-3.5-large-TurboX](https://huggingface.co/tensorart/stable-diffusion-3.5-large-TurboX) | Stability AI Community License (other) | 0.11 | |
| `sd35m-4step` | `loras/1125-18k-4.safetensors` | [tensorart/stable-diffusion-3.5-medium-turbo](https://huggingface.co/tensorart/stable-diffusion-3.5-medium-turbo) | Stability AI Community License (other) | 0.12 | |
| `sd35m-8step` | `loras/lora_sd3.5m_turbo_8steps.safetensors` | [tensorart/stable-diffusion-3.5-medium-turbo](https://huggingface.co/tensorart/stable-diffusion-3.5-medium-turbo) | Stability AI Community License (other) | 0.05 | |

`core` models are fetched first; with `FAST_START=true` ComfyUI boots as soon as they are in place and the
rest keeps downloading in the background.

## ComfyUI settings in this profile

`comfyui.args`, `comfyui.settings`, `comfyui.env` and `comfyui.extra_model_paths` are applied on Colab before
ComfyUI starts — see [docs/PROFILES.md](../../../docs/PROFILES.md#comfyui-passthrough).

## Notes

- SD 3.5 weights are under the **Stability AI Community License** (free below USD 1M annual revenue).
- SD 3.5 Medium (`sd35m-*` workflows) is **gated**: accept the license on Hugging Face and put a read token in
  `HF_TOKEN_FILE` on the gate host. The default workflows do not need it.
- `sd35l-turbox` is Tensor.Art's comparison graph; it also references `sd3.5_large_turbo`, which the kit does
  not download — run the TurboX branch.
