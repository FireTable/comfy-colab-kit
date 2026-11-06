# Profile: `flux-klein`

**FLUX.2 Klein 4B** (Distilled 4-step & Base 20-step Text-to-Image + Image Editing) and **FLUX.1 Schnell fp8** on a Google Colab L4 GPU (24 GB VRAM).

- **Workflows are never self-authored.** Every entry in [`workflows/`](workflows/) is an unmodified official template from [`Comfy-Org/workflow_templates`](https://github.com/Comfy-Org/workflow_templates) (MIT license), verified against `upstream_sha256` by `tests/test_profiles.py`.
- **Models are never vendored.** Weights are downloaded directly at runtime from official [Black Forest Labs](https://huggingface.co/black-forest-labs) and [Comfy-Org](https://huggingface.co/Comfy-Org) Hugging Face repositories into RAM-backed `tmpfs`.
- Shared text encoder (`qwen_3_4b.safetensors`), VAE (`flux2-vae.safetensors`), and compact fp8 diffusion weights (`3.79 GB` distilled + `3.81 GB` base) keep the entire default wake download at **15.40 GB** (~20 seconds on Colab L4 egress).

## Workflows

| id — App menu title | Source | License | Shipped as | Default set |
|---|---|---|---|---|
| `flux2-klein-t2i` — Fast & Quality · FLUX.2 Klein 4B T2I (official) | [Comfy-Org/flux2-klein](https://huggingface.co/Comfy-Org/flux2-klein) | MIT template; Distilled 4B Apache-2.0, Base 4B FLUX Non-Commercial | vendored `workflows/official_image_flux2_klein_text_to_image.json` | ✅ default |
| `flux2-klein-edit-distilled` — Fast · FLUX.2 Klein 4B Distilled Edit 4-step (official) | [black-forest-labs/FLUX.2-klein-4b-fp8](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8) | MIT template; weights Apache-2.0 | vendored `workflows/official_image_flux2_klein_image_edit_4b_distilled.json` | ✅ |
| `flux2-klein-edit-base` — Quality · FLUX.2 Klein 4B Base Edit 20-step (official) | [black-forest-labs/FLUX.2-klein-base-4b-fp8](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4b-fp8) | MIT template; weights FLUX Non-Commercial | vendored `workflows/official_image_flux2_klein_image_edit_4b_base.json` | ✅ |
| `flux1-schnell` — Classic · FLUX.1 Schnell fp8 4-step (official) | [Comfy-Org/flux1-schnell](https://huggingface.co/Comfy-Org/flux1-schnell) | MIT template; weights Apache-2.0 | vendored `workflows/official_flux_schnell.json` | optional |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `flux2-klein-4b-fp8` | `diffusion_models/flux-2-klein-4b-fp8.safetensors` | [black-forest-labs/FLUX.2-klein-4b-fp8](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8) | Apache-2.0 | 3.79 | core |
| `flux2-klein-base-4b-fp8` | `diffusion_models/flux-2-klein-base-4b-fp8.safetensors` | [black-forest-labs/FLUX.2-klein-base-4b-fp8](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4b-fp8) | FLUX Non-Commercial | 3.81 | core |
| `qwen3-4b` | `text_encoders/qwen_3_4b.safetensors` | [Comfy-Org/flux2-klein](https://huggingface.co/Comfy-Org/flux2-klein) | Apache-2.0 | 7.49 | core |
| `flux2-vae` | `vae/flux2-vae.safetensors` | [Comfy-Org/flux2-dev](https://huggingface.co/Comfy-Org/flux2-dev) | Apache-2.0 | 0.31 | core |
| `flux1-schnell-fp8` | `checkpoints/flux1-schnell-fp8.safetensors` | [Comfy-Org/flux1-schnell](https://huggingface.co/Comfy-Org/flux1-schnell) | Apache-2.0 | 16.05 | optional |

## Credits & Acknowledgments

- **[Black Forest Labs](https://bfl.ai/)**: Creators of the FLUX.2 Klein and FLUX.1 Schnell foundation models ([`FLUX.2-klein-4b-fp8`](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8), [`FLUX.2-klein-base-4b-fp8`](https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4b-fp8)).
- **[Comfy-Org](https://github.com/Comfy-Org)**: Authors of the official MIT workflow templates ([`Comfy-Org/workflow_templates`](https://github.com/Comfy-Org/workflow_templates)) and ComfyUI repackaged weights ([`Comfy-Org/flux2-klein`](https://huggingface.co/Comfy-Org/flux2-klein), [`Comfy-Org/flux1-schnell`](https://huggingface.co/Comfy-Org/flux1-schnell)).
- **[Qwen Team (Alibaba Cloud)](https://huggingface.co/Qwen)**: Creators of the Qwen3-4B text encoder used by FLUX.2 Klein.
