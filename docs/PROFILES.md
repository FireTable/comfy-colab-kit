# Profiles & Model Configurations

A profile is a self-contained directory with a `profile.yaml`, a `README.md`, and a `workflows/` directory. The gate dynamically loads every profile declared in `PROFILES` (comma-separated); the Wake card lets viewers select their desired model setup, with `DEFAULT_PROFILE` preselected.

JSON Schema validation specification: [`packages/schemas/profile.schema.json`](../packages/schemas/profile.schema.json).

---

## 1. Built-in Community Profiles

| Profile | Default App Workflow | Download Size | Architecture Highlights |
|---|---|---|---|
| [`moss-image`](../profiles/examples/moss-image/) *(Primary)* | `matlowai-presets` | ~25 GB core / ~57 GB full | SFT bf16 + DeBand LoRAs default, 4-step turbo graphs, official quality/layout; DeBand int8 merged, split-qkv rapid, chinoll edit (opt-in). Shared base / text encoder / VAE |
| [`flux-klein`](../profiles/examples/flux-klein/) | `flux2-klein-t2i` | ~15.4 GB core / ~27.6 GB | FLUX.2 Klein 4B Distilled (4-step Apache-2.0) & Base (20-step) T2I + `ReferenceLatent` instruction image editing, plus FLUX.1 Schnell fp8 (optional) |
| [`wan-ltx-video`](../profiles/examples/wan-ltx-video/) | `wan22-ti2v-5b` | ~16.9 GB core / ~30.6 GB | Wan 2.2 5B Text/Image-to-Video (TI2V), Wan 2.1 1.3B T2V (`prefetch: true`), and Lightricks LTX-Video 2B T2V/I2V (optional) |
| [`sd-latest`](../profiles/examples/sd-latest/) | `sd35-large` | ~15 GB core / ~22 GB | SD 3.5 Large fp8, TurboX 8-step acceleration, SD 3.5 Medium turbo (gated, optional) |
| [`qwen-image`](../profiles/examples/qwen-image/) | `qwen21-turbo-t2i` | ~27 GB core / ~43 GB | Qwen-Image 2.1 Turbo T2I, full text-to-image, and Turbo / full image editing |
| [`minimax-h3-image`](../profiles/examples/minimax-h3-image/) | `h3-t2i` | ~44.9 GB core / ~79 GB | MiniMax-H3-Image 33.1B DiT W4A8 (L4-optimized zero-OOM), Qwen3-VL 32B INT8, 6-step native 4K T2I & Multi-Ref Edit |
| [`minimax-h3-video`](../profiles/examples/minimax-h3-video/) | `h3-r2v` | ~39.63 GB core (~32.76 GB L4) / ~49.25 GB full | MiniMax-H3 TriPRISM 8-step TurboFusion (`A100` 80G `--high-mem` → `G4` 96G → `L4` 24G auto-fallback via `dit-triprism-w6a8`; per-GPU `megapixels` via `overrides_by_gpu`; `video-vae-fp16` on A100/G4); 6 workflows: Ref2V/I2V/T2V w/ native stereo audio, `h3-extend` (`ColorTransfer` anchor + RelayVSR ×2), `h3-extender-latent` (zero-MP4 latent chaining), `h3-controlnet` Fun ControlNet-Union 2.0 depth/pose/canny V2V |

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

### Multi-GPU Fallback & Per-GPU Overrides (`fallback_accelerators`, `high_mem`, `model_aliases_by_gpu`, `overrides_by_gpu`)

Profiles can declare resilient multi-GPU allocation, tier-specific weight substitution, and per-GPU workflow widget overrides:

```yaml
accelerator: A100
high_mem: true
fallback_accelerators: [G4, L4]
model_aliases_by_gpu:
  L4:
    Sulphur3_10ErosMax_SingularityV2_TriPRISM_TurboFusion_realism_int8_convrot.safetensors: dit-triprism-tiny
    minimax_h3_video_vae_fp16.safetensors: video-vae-int8
workflows:
  - id: h3-r2v
    overrides_by_gpu:
      L4: [{node: 115, widget_index: 1, value: 0.4}]
      A100: [{node: 115, widget_index: 1, value: 0.8}]
      G4: [{node: 115, widget_index: 1, value: 0.9}]
```

- **`fallback_accelerators`**: Ordered fallback GPU tiers (`L4`, `A100`, `G4`, `T4`, `H100`). When Colab returns `503` or capacity errors, `comfy-gate` reconciles active sessions, retries with jittered backoff, and falls back down the chain (overridable per wake via `POST /_gate/wake` `accelerator`).
- **`high_mem`**: Boolean (default `false`); passes `--high-mem` to `colab new` for `A100` (80 GB VRAM, `2,039 GB/s`, `~7.2 CU/h`), `T4`, or `CPU` and enables `high_mem`-aware CU accounting.
- **`model_aliases_by_gpu`**: Maps GPU tier → `{upstream_filename: model_id}` (merged over `model_aliases` inside `build_plan(..., actual_accelerator=...)` once allocation succeeds), so a 24 GB `L4` fallback downloads only its compatible smaller checkpoint (`core: true`) instead of an 80 GB/96 GB tier model.
- **Workflow `overrides_by_gpu`**: Optional per-workflow map of GPU tier → `[{node, widget_index, value}]`, merged over `overrides` inside `build_plan(..., actual_accelerator=...)` (matching keys `(node, widget_index)` overwrite base overrides). Ideal for scaling default `megapixels` or step counts automatically based on the GPU actually allocated.

---

## 4. Step-by-Step: Authoring a Custom Profile

> [!TIP]
> **Let an AI Agent Scaffold Your Profile (or Adapt a Fork for Other Colab Workloads)**:
> Instead of writing `profile.yaml` by hand, you can give your AI coding agent (Claude Code, Cursor, Codex, OpenClaw) a Hugging Face model/workflow URL and point it at [**`docs/AGENT_SCAFFOLDING.md`**](AGENT_SCAFFOLDING.md). That guide also explains how to fork this repository and let an AI agent reuse the gateway architecture for *any* Google Colab GPU service (vLLM, Ollama, Forge, Gradio, etc.).

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
     args:
       - --preview-method
       - auto
       - --highvram
       - --reserve-vram
       - '0.2'
     env:
       PYTORCH_CUDA_ALLOC_CONF: expandable_segments:True
       CUDA_MODULE_LOADING: LAZY
       TORCH_CUDNN_V8_API: '1'
       TORCH_ALLOW_TF32_CUBLAS_OVERRIDE: '1'
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
         inputs:
           - [3, text, "正向提示词 (Positive Prompt)"]  # [node_id, widget_name, optional_label]
         outputs: [9]
   app_default: default-gen
   ```
  > [!TIP]
  > `app.inputs` supports either 2-tuples `[node_id, widget_name]` (automatic label inference) or 3-tuples `[node_id, widget_name, custom_label]`. For full App Mode serialization invariants and schema rules, see [`docs/APP_MODE.md`](APP_MODE.md).

4. **Validate the profile**:
   ```bash
   python scripts/validate-profile.py profiles/custom/my-model/
   ```
   *Expected output*: `ok  my-model       1 workflows, wake download ~12.50 GB`

---

## 🌟 Upstream Credits & Model Acknowledgments

We celebrate the creators and open-source communities behind the profiles supported in this repository:
- **MOSS-Image**: Created by [OpenLMLab](https://openlmlab.org/) and [MATLOWAI](https://huggingface.co/MATLOWAI). DeBand LoRAs and int8 quantization workflows provide exceptional visual fidelity on Colab L4 GPUs.
- **FLUX.2 Klein 4B & FLUX.1 Schnell**: Created by [Black Forest Labs](https://blackforestlabs.ai/). Open Apache-2.0 4-step distilled and 20-step base diffusion checkpoints and official ComfyUI templates for fast text-to-image and instruction editing.
- **Wan 2.2 5B TI2V & Wan 2.1 1.3B**: Created by the [Alibaba Wan Team (Tongyi Lab)](https://github.com/Wan-Video/Wan2.2). Cutting-edge Apache-2.0 open-weight video models enabling text-to-video and first-frame image-to-video directly on consumer and cloud GPUs.
- **Lightricks LTX-Video 2B**: Created by [Lightricks](https://github.com/Lightricks/LTX-Video). Lightweight high-frame-rate DiT video model for text/image-to-video synthesis.
- **Stable Diffusion 3.5**: Created by [Stability AI](https://stability.ai/). High-performance FP8 and TurboX community acceleration graphs.
- **Qwen-Image**: Developed by the [Qwen Team](https://github.com/QwenLM) at Alibaba Cloud. Fast visual synthesis and multimodal generation.
- **MiniMax-H3-Image**: Created by [MiniMax](https://github.com/MiniMax-AI/MiniMax-H3-Image) and repackaged by [Comfy-Org](https://huggingface.co/Comfy-Org/MiniMax-H3-Image). 33.1B single-stream DiT architecture delivering native 16 MP image generation and multi-reference editing in 6 distilled sampling steps.
- **MiniMax-H3-Video (TriPRISM)**: Built on [MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) and [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates), powered by the TriPRISM TurboFusion 8-step realism merges (`Sulphur-3` + `10Eros-Max` + `Singularity V2`) by **OliviaRossi**, **TenStrip**, **SulphurAI**, and **WarmBloodAban**, **Alibaba PAI VideoX-Fun** (`MiniMax-H3-Fun-Controlnet-Union` depth/pose/canny video-to-video), and **tritant** ([ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender), Apache-2.0 continuation extender).
- **Community Custom Nodes**: We thank **Kijai** and the broad ComfyUI node contributor ecosystem for publishing optimized sampling nodes and adapters.
