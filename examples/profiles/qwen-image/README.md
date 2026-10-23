# Profile: `qwen-image`

Qwen-Image 2.1 (Comfy-Org official templates) on a Colab L4. The App-mode default is **2.1 Turbo T2I**;
full-step T2I and the Turbo / full Edit templates are also included. Weights are int8 so everything fits 24 GB.

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
| `qwen21-turbo-t2i` — Fast · Qwen-Image 2.1 Turbo T2I (official) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | MIT template; weights qwen-research license (check commercial) | vendored `workflows/official_image_qwen_image_2_1_turbo_t2i.json` | TIER1 speed (latest) | ✅ default |
| `qwen21-t2i` — Quality · Qwen-Image 2.1 T2I (official) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | MIT template; weights qwen-research license (check commercial) | vendored `workflows/official_image_qwen_image_2_1_t2i.json` | TIER1 quality | ✅ |
| `qwen21-turbo-edit` — Fast · Qwen-Image 2.1 Turbo Edit (official) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | MIT template; weights qwen-research license (check commercial) | vendored `workflows/official_image_qwen_image_2_1_turbo_image_edit.json` | TIER1 edit speed | ✅ |
| `qwen21-edit` — Quality · Qwen-Image 2.1 Edit (official) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | MIT template; weights qwen-research license (check commercial) | vendored `workflows/official_image_qwen_image_2_1_image_edit.json` | TIER1 edit quality | optional |

Optional workflows are enabled per wake: `POST /_gate/wake {"profile": "qwen-image", "workflows": ["<id>"]}` or
`ckit wake --profile qwen-image --workflow <id>` (see [docs/AGENT_SKILL.md](../../../docs/AGENT_SKILL.md)).

### Considered and excluded

| Candidate | Reason |
|---|---|
| Qwen-Image-Edit 2511 (bf16 / int8) | Tier 2: ~20.5 GB weights are too tight for 24 GB without offload; 2.1 Edit preferred |
| Qwen-Image 2512 (+ 2-step LoRA) | Tier 3: superseded by 2.1 |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `q21-turbo-int8` | `diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | qwen-research license (see model card; check commercial use) | 7.26 | core |
| `q21-int8` | `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | qwen-research license (see model card; check commercial use) | 7.26 | |
| `q3vl-te` | `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | qwen-research license (see model card; check commercial use) | 9.35 | core |
| `pe-t2i` | `text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | qwen-research license (see model card; check commercial use) | 9.47 | core |
| `pe-i2i` | `text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | qwen-research license (see model card; check commercial use) | 9.47 | |
| `q21-vae` | `vae/qwen_image_2.1_vae_bf16.safetensors` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | qwen-research license (see model card; check commercial use) | 0.68 | core |

`core` models are fetched first; with `FAST_START=true` ComfyUI boots as soon as they are in place and the
rest keeps downloading in the background.

## ComfyUI settings in this profile

`comfyui.args`, `comfyui.settings`, `comfyui.env` and `comfyui.extra_model_paths` are applied on Colab before
ComfyUI starts — see [docs/PROFILES.md](../../../docs/PROFILES.md#comfyui-passthrough).

## Notes

- Qwen-Image weights use the **qwen-research** license; check the model card before commercial use.
- The 2.1 templates keep `SaveImage` inside a subgraph, so App mode shows the subgraph outputs
  (`app.outputs` is intentionally empty).
