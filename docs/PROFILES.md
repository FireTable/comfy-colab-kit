# Profiles

A profile is a folder with a `profile.yaml`, a `README.md` and a `workflows/` folder. The gate loads every
folder listed in `PROFILES` (comma separated); the wake page lets you choose one, `DEFAULT_PROFILE` is
preselected. Schema: [`packages/schemas/profile.schema.json`](../packages/schemas/profile.schema.json) (JSON Schema 2020-12);
the gate additionally runs the checks in `apps/gate/profiles.py`. Validate before committing:

```bash
python scripts/validate-profile.py profiles/examples/*/
```

Shipped profiles:

| Profile | Default App workflow | Notes |
|---|---|---|
| [`moss-image`](../profiles/examples/moss-image/) (primary) | `matlowai-presets` | SFT bf16 + DeBand LoRAs (default), 4-step turbo graphs, official quality / layout; DeBand int8 merged, split-qkv rapid, Base, chinoll edit (opt-in). One shared base / text encoder / VAE |
| [`sd-latest`](../profiles/examples/sd-latest/) | `sd35-large` | SD 3.5 Large fp8, TurboX 8-step, SD 3.5 Medium turbo (gated, optional) |
| [`qwen-image`](../profiles/examples/qwen-image/) | `qwen21-turbo-t2i` | Qwen-Image 2.1 Turbo / full T2I and Edit |

## Workflow policy

1. **Never author workflows.** Only community-published or official workflows, unchanged.
2. **Vendor only when the license allows it** (Apache-2.0, MIT official templates): put the file in
   `workflows/`, reference it with `file:` and record `upstream_url` + `upstream_sha256`
   (`tests/test_profiles.py` checks the hash and the license).
3. **URL-only otherwise** (no license, GPL code, non-OSI licenses, gated repos): reference with `url:`; the file
   is fetched from the author at setup time.
4. **Models are always URL-only** (`hf://owner/repo[@rev]/path` or `https://`), with SHA-256 when known.
5. Each profile README carries the source / license table.

## `profile.yaml` reference

```yaml
id: moss-image                      # [a-z0-9-], unique
title: MOSS-Image (…)
accelerator: L4
comfyui:                            # required
  repo: https://github.com/comfyanonymous/ComfyUI
  commit: bc5f880                   # pinned ComfyUI commit
  args: [--preview-method, auto]    # see "ComfyUI passthrough"
  settings: {}                      # merged into comfy.settings.json
  env: {}                           # extra environment for the ComfyUI process
  extra_model_paths: {}             # written to extra_model_paths.yaml
frontend:
  package: comfyui-frontend-package==1.57.1
models:
  - id: deband-int8
    dest: diffusion_models/moss_image_sft_deband_int8_convrot.safetensors   # under ComfyUI/models/
    url: hf://MATLOWAI/MOSS-Image-SFT-DeBand-int8/moss_image_sft_deband_int8_convrot.safetensors
    license: apache-2.0
    sha256: 2508…                   # LFS oid; verified for non-HF URLs
    size_gb: 5.41
    core: true                      # fetched first; FAST_START boots after core models
    gated: false                    # true = needs HF_TOKEN_FILE on the gate
custom_nodes:
  - id: rapid-moss
    url: https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora   # HF repo (+ subdir) or git repo
    subdir: comfyui/rapid-moss
    ref: null                       # optional git ref
    license: apache-2.0
workflows:
  - id: matlowai-deband-int8
    title: Default · DeBand int8 presets
    source: https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8   # author page (required)
    license: Apache-2.0             # required
    file: workflows/matlowai_deband_int8.json   # vendored …
    # url: https://…/workflow.json             # … or URL-only
    upstream_url: https://…/moss_deband_int8.json
    upstream_sha256: …
    format: json                    # json | png (workflow embedded in a PNG) | builtin
    optional: false                 # optional workflows are only installed when requested at wake
    prefetch: false                 # true = download this workflow's models in the background on every wake
    models: [deband-int8, te-int8, vae]
    custom_nodes: []
    loras: [hf://owner/repo/style.safetensors]  # extra LoRAs downloaded to models/loras
    overrides:                      # swap a widget value (e.g. which file a loader loads)
      - {node: 12, widget_index: 0, value: other_lora.safetensors}
    app:                            # ComfyUI App mode (extra.linearData)
      inputs: [[6, value], [1, choice]]   # [node id, widget name]
      outputs: [18]                 # node ids; may be empty when outputs live inside a subgraph
    note: free text
app_default: matlowai-deband-int8   # opened first; its models count as core
smoke:                              # optional end-to-end check before READY
  api_url: https://…/moss_deband_int8_api.json
  license: apache-2.0
  models: [deband-int8, te-int8, vae]
  timeout_s: 600
```

Rejected by validation: secret-like keys or values anywhere (`token`, `secret`, `password`, `api_key`, HF / CF
token patterns), URLs with query strings, `dest` outside the known model folders or with `..`, a `file:`
outside `workflows/` or missing, unknown model / node ids, a missing `license` or `source`.

## App mode

For each workflow the kit writes ComfyUI's own App-mode metadata at install time:
`extra.linearData = {"inputs": app.inputs, "outputs": app.outputs}` and `extra.linearMode = (DEFAULT_VIEW == "app")`.
Workflows are saved as `user/default/workflows/<id>.json`; `user/default/kit.json` records the default
workflow and view, which the overlay opens on first load. Users switch
views with the overlay's **App ⇄ Graph** button (`Comfy.ToggleLinear`, Alt+M).

## ComfyUI passthrough

The `comfyui:` block configures ComfyUI itself on Colab before it starts:

| Key | Type | Applied as |
|---|---|---|
| `args` | list of strings | appended to `python main.py …`. Must start with a `--flag`. Reserved (managed by the kit): `--listen`, `--port`, `--output-directory`, `--disable-auto-launch`, `--extra-model-paths-config` |
| `settings` | mapping | merged into `user/default/comfy.settings.json` (e.g. `Comfy.LinkRenderMode`) |
| `env` | mapping `UPPER_CASE: string` | added to the ComfyUI process environment. Reserved: `CF_TUNNEL_TOKEN`, `HF_TOKEN`, `KIT_GATE_URL`, `KIT_PROFILE_JSON`; secret-like names are rejected |
| `extra_model_paths` | mapping | written as `extra_model_paths.kit.yaml` and passed with `--extra-model-paths-config` |
| `repo`, `commit` | string | ComfyUI source and pinned commit |

Example (`moss-image`):

```yaml
comfyui:
  args: [--preview-method, auto, --reserve-vram, "1"]
  settings: {Comfy.LinkRenderMode: 2}
  env: {PYTORCH_CUDA_ALLOC_CONF: "expandable_segments:True"}
```

## Choosing workflows at wake

`POST /_gate/wake {"profile": "moss-image", "workflows": ["kijai-4step"]}` installs the non-optional workflows
plus the listed optional ones (`ckit wake --profile moss-image --workflow kijai-4step`).
Switching profile is a cold restart (`POST /_gate/switch`).


## What a wake downloads

Only the **default workflow's** models (and the smoke test's) are downloaded on wake; they are *core* and boot
waits for them. Every other installed workflow is **on demand**: its JSON is installed, its models are fetched
when someone asks (`POST /_gate/fetch {"workflows": [...]}`, listed in status `models.on_demand`). A workflow with
`prefetch: true`, or one passed in the wake body (`{"workflows": [...]}`), downloads in the background after the
core files. Background downloads pause (SIGSTOP) while ComfyUI executes a prompt and resume afterwards. Each HF
file has a stall watchdog: no new bytes for 45 s (`KIT_STALL_S`) → that file's download is killed and restarted
(partial chunks are kept). 429 / 5xx / connection errors retry with backoff.

## Sharing weights: `model_aliases`

Upstream workflow JSONs are never edited. To make several workflows share one set of weights, map the filename an
upstream workflow loads to a model id:

```yaml
model_aliases:
  moss_image_sft_int8_convrot.safetensors: sft-bf16   # official templates load int8 -> use the shared bf16
  qwen3.5_9b_w4a8_gptq.safetensors: te-int8
  flux2-vae_small.safetensors: vae
```

At install time `setup.py` rewrites every string widget equal to a key (all nodes, subgraph definitions included;
also the smoke API prompt) to the mapped model's filename. Only use it where the workflow works with the other file
by a pure filename change (same architecture / loader). A workflow that truly needs a different checkpoint (merged
fine-tune, split-qkv layout, Base vs SFT) keeps its own model and should be `optional: true`.

moss-image uses SFT bf16 (10.2 GB) + Qwen3.5-9B int8 (9.8 GB) + Flux2 VAE (0.3 GB): the highest precision that fits
a 24 GB L4 together (the bf16 text encoder alone is 19.3 GB). Default wake: 24.9 GB; all non-optional workflows:
27.3 GB; everything including opt-in: 66.9 GB.
