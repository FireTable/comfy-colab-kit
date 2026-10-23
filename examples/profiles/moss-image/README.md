# Profile: `moss-image`

MOSS-Image (OpenMOSS, Apache-2.0) on a Colab L4. **Primary profile.** The App-mode default is the
MATLOWAI **banding-fix presets** workflow (`matlowai-presets`): SFT **bf16** + DeBand LoRAs, with a preset switch
for stronger banding fix and DMD/DMAD fast modes (14 / 10 steps). Every workflow shares **one** base, text encoder and
VAE — SFT bf16 (10.2 GB) + Qwen3.5-9B int8 (9.8 GB) + Flux2 VAE (0.3 GB), the highest precision that fits a 24 GB L4
together (the bf16 text encoder alone is 19.3 GB). A plain wake downloads **24.9 GB**; other workflows' extra files
(a LoRA or adapter, ~1.2 GB) are fetched on demand.

- **Workflows are never self-authored.** Each entry is an unmodified upstream file. It is *vendored* under
  [`workflows/`](workflows/) only when its license allows redistribution (Apache-2.0 or the MIT official
  templates); everything else is *URL-only* and fetched from the author at setup time. `upstream_sha256`
  pins the reviewed version (vendored files are checked by `tests/test_profiles.py`).
- **Models are never vendored.** Every model is downloaded from its original Hugging Face repo; SHA-256 values
  are the LFS object ids at review time.
- The kit only adds ComfyUI's App-mode metadata (`extra.linearData`, `extra.linearMode`), swaps file names
  declared under `overrides`, and applies the profile's `model_aliases` (official templates load SFT int8 /
  Qwen w4a8 / small VAE → remapped to the shared SFT bf16 / Qwen int8 / Flux2 VAE at install time; the JSON files
  stay byte-identical to upstream).

Source of truth: the reviewed workflow manifest (2026-10-23), mirrored in [`profile.yaml`](profile.yaml).

## Workflows

| id — App menu title | Source | License | Shipped as | Tier | Default set |
|---|---|---|---|---|---|
| `matlowai-deband-int8` — DeBand int8 merged checkpoints (25 / Fast 10 steps) | [MATLOWAI/MOSS-Image-SFT-DeBand-int8](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8) | Apache-2.0 | vendored `workflows/matlowai_deband_int8.json` | TIER1 own merged int8 checkpoints (3 × 5.4 GB) | optional |
| `akhaliq-4step` — Fast · 4-step turbo LoRA (akhaliq) | [akhaliq/MOSS-Image-SFT-4-steps](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps) | Apache-2.0 (redistributable) | vendored `workflows/akhaliq_moss-image-4step-turbo.json` | TIER1 speed | ✅ |
| `rapid-moss-4step` — Fast · rapid-moss 4-step adapter (darask0) | [darask0/MOSS-Image_4-step_adapter_lora](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora) | Apache-2.0 | vendored `workflows/rapid-moss-4step-native.json` | TIER1 speed (best quality at 4 steps, ~1MP only) | ✅ |
| `official-t2i` — Quality · official Text to Image (Comfy-Org) | [https://docs.comfy.org/tutorials/image/moss-image/moss-image](https://docs.comfy.org/tutorials/image/moss-image/moss-image) | MIT templates; model Apache-2.0 | vendored `workflows/official_moss_t2i.json` | TIER1 quality reference | ✅ |
| `official-layout` — Layout · official Layout-Guided (Comfy-Org) | [https://docs.comfy.org/tutorials/image/moss-image/moss-image](https://docs.comfy.org/tutorials/image/moss-image/moss-image) | MIT/Apache-2.0 | vendored `workflows/official_moss_t2i_bboxes.json` | TIER1 layout | ✅ |
| `matlowai-presets` — Default · DeBand banding-fix LoRAs on SFT bf16 (+ DMD/DMAD fast modes) | [MATLOWAI/MOSS-Image-SFT-DeBand](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand) | Apache-2.0 | vendored `workflows/matlowai_banding_fix_presets.json` | TIER1 quality+speed balance | ✅ default |
| `kijai-4step` — Fast · Kijai 4-step LoRA v2 (akhaliq graph, LoRA swapped) | [akhaliq/MOSS-Image-SFT-4-steps](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps) | workflow Apache-2.0; LoRA unspecified (link-only) | URL-only ([upstream](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps/resolve/main/comfyui/moss-image-4step-turbo.json)) | TIER1 speed | optional |
| `matlowai-presets-rapid` — Presets + rapid-moss branch (heavy) | [MATLOWAI/MOSS-Image-SFT-DeBand](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand) | Apache-2.0 | vendored `workflows/matlowai_banding_fix_presets_rapid_moss.json` | TIER2 (needs split-qkv bf16 base, +10.2 GB) | optional |
| `official-base-t2i` — Base · official Base Text to Image | [https://docs.comfy.org/tutorials/image/moss-image/moss-image](https://docs.comfy.org/tutorials/image/moss-image/moss-image) | MIT/Apache-2.0 | vendored `workflows/official_moss_base_t2i.json` | TIER2 (Base checkpoint bf16, +10.2 GB) | optional |
| `chinoll-edit-single` — Edit · single reference (chinoll) | [Chenkin-x/ComfyUI-Moss-Image-Edit](https://github.com/Chenkin-x/ComfyUI-Moss-Image-Edit) | code GPL-3.0; weights unlicensed -> link only, don't bundle | URL-only ([upstream](https://raw.githubusercontent.com/Chenkin-x/ComfyUI-Moss-Image-Edit/main/workflows/moss_edit_single_reference.json)) | TIER1 edit | optional |
| `chinoll-edit-multi` — Edit · multi reference (chinoll) | [Chenkin-x/ComfyUI-Moss-Image-Edit](https://github.com/Chenkin-x/ComfyUI-Moss-Image-Edit) | GPL-3.0 code | URL-only ([upstream](https://raw.githubusercontent.com/Chenkin-x/ComfyUI-Moss-Image-Edit/main/workflows/moss_edit_multi_reference.json)) | TIER1 edit | optional |

Non-default workflows are installed but their extra models are **on demand**: `POST /_gate/fetch {"workflows":
["<id>"]}` on a running session (status `models.on_demand` lists what is missing). Optional workflows are enabled
per wake: `POST /_gate/wake {"profile": "moss-image", "workflows": ["<id>"]}` or `ckit wake --profile moss-image
--workflow <id>` (see [docs/AGENT_SKILL.md](../../../docs/AGENT_SKILL.md)); their models then download in the
background. `prefetch: true` on a workflow does the same on every wake.

### Considered and excluded

| Candidate | Reason |
|---|---|
| `rapid-moss-4step.json` (HF-loader variant) | Tier 3: the native-loader variant is preferred |
| `openmoss_plugin_moss-image.json` | Tier 3: legacy plugin, superseded by native templates |

## Models

| id | Destination (`models/…`) | Repo | License | GB | Flags |
|---|---|---|---|---|---|
| `deband-int8` | `diffusion_models/moss_image_sft_deband_int8_convrot.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand-int8](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8) | apache-2.0 | 5.41 | on demand |
| `deband-plus-int8` | `diffusion_models/moss_image_sft_deband_plus_int8_convrot.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand-int8](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8) | apache-2.0 | 5.41 | on demand |
| `deband-fast-int8` | `diffusion_models/moss_image_sft_deband_fast_dmd035_int8_convrot.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand-int8](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8) | apache-2.0 | 5.41 | on demand |
| `te-int8` | `text_encoders/qwen3.5_9b_int8_convrot.safetensors` | [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image) | apache-2.0 | 9.75 | wake (core) |
| `vae` | `vae/flux2-vae.safetensors` | [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image) | apache-2.0 | 0.34 | wake (core) |
| `sft-bf16` | `diffusion_models/moss_image_sft_bf16.safetensors` | [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image) | apache-2.0 | 10.16 | wake (core) |
| `base-bf16` | `diffusion_models/moss_image_base_bf16.safetensors` | [Comfy-Org/MOSS-Image](https://huggingface.co/Comfy-Org/MOSS-Image) | apache-2.0 | 10.16 | on demand |
| `akhaliq-4step-v2` | `loras/moss_image_sft_4step_v2.0.safetensors` | [akhaliq/MOSS-Image-SFT-4-steps](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps) | apache-2.0 | 1.19 | on demand |
| `kijai-4step-v2` | `loras/moss_image_sft_4_steps_lora_v2_600_avg_rank_72_bf16.safetensors` | [Kijai/MOSS-image_experimental](https://huggingface.co/Kijai/MOSS-image_experimental) | unspecified (link-only, never mirrored) | 0.33 | on demand |
| `rapid-4step-lora` | `rapid_moss/rapid-moss-4step/lora.safetensors` | [darask0/MOSS-Image_4-step_adapter_lora](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora) | apache-2.0 | 1.19 | on demand |
| `rapid-4step-time` | `rapid_moss/rapid-moss-4step/time_embedding.safetensors` | [darask0/MOSS-Image_4-step_adapter_lora](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora) | apache-2.0 | 0.04 | on demand |
| `rapid-4step-dataspace` | `rapid_moss/rapid-moss-4step/dataspace.safetensors` | [darask0/MOSS-Image_4-step_adapter_lora](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora) | apache-2.0 | 0.01 | on demand |
| `lodestones-8step-lora` | `rapid_moss/lodestones-8step-latest/lora.safetensors` | [lodestones/MOSS-Image-SFT-8-steps-dmd](https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd) | apache-2.0 | 1.19 | on demand |
| `lodestones-8step-time` | `rapid_moss/lodestones-8step-latest/time_embedding.safetensors` | [lodestones/MOSS-Image-SFT-8-steps-dmd](https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd) | apache-2.0 | 0.04 | on demand |
| `lodestones-8step-dataspace` | `rapid_moss/lodestones-8step-latest/dataspace.safetensors` | [lodestones/MOSS-Image-SFT-8-steps-dmd](https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd) | apache-2.0 | ? | on demand |
| `lodestones-8step-as-lora` | `loras/lodestones_moss_sft_8step_dmd_lora_latest.safetensors` | [lodestones/MOSS-Image-SFT-8-steps-dmd](https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd) | apache-2.0 | 1.19 | wake (core) |
| `darask0-4step-as-lora` | `loras/darask0_moss_sft_4step_dmad_lora.safetensors` | [darask0/MOSS-Image_4-step_adapter_lora](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora) | apache-2.0 | 1.19 | wake (core) |
| `deband-l37-41` | `loras/moss_image_sft_banding_fix_L37-41_bf16.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand) | apache-2.0 | 1.13 | wake (core) |
| `deband-l32-36` | `loras/moss_image_sft_banding_fix_L32-36_extra_bf16.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand) | apache-2.0 | 1.13 | wake (core) |
| `deband-l37-41-splitqkv` | `loras/moss_image_sft_banding_fix_L37-41_bf16_splitqkv.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand) | apache-2.0 | 1.13 | on demand |
| `sft-bf16-splitqkv` | `diffusion_models/moss_image_sft_bf16_split_qkv.safetensors` | [MATLOWAI/MOSS-Image-SFT-DeBand](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand) | apache-2.0 | 10.16 | on demand |
| `chinoll-edit-patch` | `model_patches/moss_image_edit.safetensors` | [chinoll/Moss-Image-Edit](https://huggingface.co/chinoll/Moss-Image-Edit) | unspecified (link-only, never mirrored) | 0.3 | on demand |

`wake (core)` models are the default workflow's and the smoke test's; boot waits for them. Everything else is
downloaded only when a workflow that needs it is fetched (or prefetched). Background downloads pause while a prompt
is running. Model aliases: `moss_image_sft_int8_convrot` → `sft-bf16`, `qwen3.5_9b_w4a8_gptq` / `qwen3.5_9b_bf16` →
`te-int8`, `flux2-vae_small` → `vae`, `moss_image_base_int8_convrot` → `base-bf16`.

## ComfyUI settings in this profile

`comfyui.args`, `comfyui.settings`, `comfyui.env` and `comfyui.extra_model_paths` are applied on Colab before
ComfyUI starts — see [docs/PROFILES.md](../../../docs/PROFILES.md#comfyui-passthrough).

## Prompting tips (community consensus)

- Describe the finished image, not an instruction; put text to render in double quotes; add line breaks yourself.
- Stay in 1–2 MP, width/height multiples of 16.
- Few-step LoRAs: CFG 1, empty negative; use only one acceleration LoRA at a time.
- Use the layout workflow when placement matters; turn the prompt upsampler off to keep your wording.
- SLG can over-expose anime styles; vertical banding on SFT → the DeBand workflows.

Sources: docs.comfy.org MOSS-Image guide, the model cards linked above, mossimage.org/settings.
