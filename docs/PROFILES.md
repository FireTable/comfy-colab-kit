# Profiles & Model Configurations

A profile is a self-contained directory with a `profile.yaml`, a `README.md`, and a `workflows/` directory. The gate dynamically loads every profile declared in `PROFILES` (comma-separated); the Wake card lets viewers select their desired model setup, with `DEFAULT_PROFILE` preselected.

JSON Schema validation specification: [`packages/schemas/profile.schema.json`](../packages/schemas/profile.schema.json).

---

## 1. Built-in Community Profiles

| Profile | Default App Workflow | Download Size | Architecture Highlights |
|---|---|---|---|
| [`moss-image`](../profiles/examples/moss-image/) *(Primary)* | `matlowai-presets` | ~26 GB core / ~57 GB full | SFT bf16 + DeBand LoRAs default, 4-step turbo graphs, official quality/layout; DeBand int8 merged, split-qkv rapid, chinoll edit (opt-in). Shared base / text encoder / VAE |
| [`sd-latest`](../profiles/examples/sd-latest/) | `sd35-large` | ~15 GB core / ~22 GB | SD 3.5 Large fp8, TurboX 8-step acceleration, SD 3.5 Medium turbo (gated, optional) |
| [`qwen-image`](../profiles/examples/qwen-image/) | `qwen21-turbo-t2i` | ~27 GB core / ~43 GB | Qwen-Image 2.1 Turbo T2I, full text-to-image, and Turbo / full image editing |

---

## 2. Workflow Policy & Licensing Rules

1. **Never author original workflows**: Only community-published or official workflows are used, preserved in their authentic state.
2. **Vendor only with permissive OSI licenses**: If a workflow is Apache-2.0 or MIT, place the JSON file in `workflows/`, reference it via `file:`, and record `upstream_url` + `upstream_sha256`.
3. **Reference by URL otherwise**: If a workflow lacks a license, has GPL dependencies, or is from a gated repo, reference it via `url:`; it is downloaded directly from the author during Colab setup.
4. **Weights are strictly URL-only**: Model checkpoints and LoRAs are never stored in git. They are fetched on-demand using public URLs (`hf://owner/repo[@rev]/path` or `https://`), verified by SHA-256.

---

## 3. Weight Sharing via `model_aliases`

Different community workflows frequently reference identical model checkpoints under disparate filenames (for example, one workflow loads `moss_image_sft_int8_convrot.safetensors`, while an official template references `moss_image_sft_bf16.safetensors`).

To prevent wasting network bandwidth and disk space downloading duplicate weights, Comfy Colab Kit uses in-memory JSON widget rewriting via `model_aliases`:

```yaml
model_aliases:
  moss_image_sft_int8_convrot.safetensors: sft-bf16
  qwen3.5_9b_w4a8_gptq.safetensors: te-int8
  flux2-vae_small.safetensors: vae
```

```
Community Workflow JSON ──► setup.py parses all loader nodes ──► Matches "moss_image_sft_int8_convrot.safetensors"
                                                                         │
                                                                         ▼
                                                       Rewrites loader widget to use:
                                                       "moss_image_sft_bf16.safetensors" (sft-bf16)
```

- Upstream workflow files remain completely unmodified in git.
- During Colab bootstrap (`packages/colab/setup.py`), the loader widget values in node graphs and subgraph definitions are dynamically rewritten to reference the shared local model file.
- Result: You download the high-precision base weights once, and all compatible community workflows share them seamlessly.

---

## 4. Step-by-Step: Authoring a Custom Profile

To create your own model profile (e.g. for a custom Flux or SDXL setup):

1. **Create the profile directory**:
   ```bash
   mkdir -p profiles/custom/my-model/workflows
   ```
2. **Download an official or community workflow JSON**:
   Place the workflow file in `profiles/custom/my-model/workflows/default.json`. Verify that its license is Apache-2.0 or MIT.
3. **Draft `profiles/custom/my-model/profile.yaml`**:
   ```yaml
   id: my-model
   title: My Custom Model Setup
   accelerator: L4
   comfyui:
     repo: https://github.com/comfyanonymous/ComfyUI
     commit: bc5f880
     args: [--preview-method, auto]
   frontend:
     package: comfyui-frontend-package==1.57.1
   models:
     - id: base-checkpoint
       dest: checkpoints/my_model.safetensors
       url: https://huggingface.co/owner/repo/resolve/main/my_model.safetensors
       license: mit
       size_gb: 12.5
       core: true
   workflows:
     - id: default-gen
       title: Default Generation
       source: https://huggingface.co/owner/repo
       license: MIT
       file: workflows/default.json
       upstream_url: https://huggingface.co/owner/repo/raw/main/workflow.json
       upstream_sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
       models: [base-checkpoint]
       app:
         inputs: [[3, "text"]]
         outputs: [9]
   app_default: default-gen
   ```
4. **Validate the profile**:
   ```bash
   python scripts/validate-profile.py profiles/custom/my-model/
   ```
   *Expected output*: `ok  my-model       1 workflows, wake download ~12.50 GB`

---

## 🌟 Upstream Credits & Model Acknowledgments

We celebrate the creators and open-source communities behind the profiles supported in this repository:
- **MOSS-Image**: Created by [OpenLMLab](https://openlmlab.org/) and [MATLOWAI](https://huggingface.co/MATLOWAI). DeBand LoRAs and int8 quantization workflows provide exceptional visual fidelity on Colab L4 GPUs.
- **Stable Diffusion 3.5**: Created by [Stability AI](https://stability.ai/). High-performance FP8 and TurboX community acceleration graphs.
- **Qwen-Image**: Developed by the [Qwen Team](https://github.com/QwenLM) at Alibaba Cloud. Fast visual synthesis and multimodal generation.
- **Community Custom Nodes**: We thank **Kijai** and the broad ComfyUI node contributor ecosystem for publishing optimized sampling nodes and adapters.
