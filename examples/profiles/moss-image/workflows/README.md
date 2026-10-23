# `moss-image` workflows

Unmodified upstream workflow files, redistributed under their original licenses (Apache-2.0 or MIT).
Do not edit them by hand — App-mode metadata and `overrides` are applied by `colab/setup.py` at install time.
URL-only workflows (licenses that do not allow or do not state redistribution) are **not** stored here.

| File | Workflow id | Upstream | License | sha256 |
|---|---|---|---|---|
| `matlowai_deband_int8.json` | `matlowai-deband-int8` | [link](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand-int8/resolve/main/workflows/moss_deband_int8.json) | Apache-2.0 | `3fb36959a80c…` |
| `akhaliq_moss-image-4step-turbo.json` | `akhaliq-4step` | [link](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps/resolve/main/comfyui/moss-image-4step-turbo.json) | Apache-2.0 (redistributable) | `1c4d3068ed98…` |
| `rapid-moss-4step-native.json` | `rapid-moss-4step` | [link](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora/resolve/main/comfyui/rapid-moss/example_workflows/rapid-moss-4step-native.json) | Apache-2.0 | `3382f3dfe8b8…` |
| `official_moss_t2i.json` | `official-t2i` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_moss_image_t2i.json) | MIT templates; model Apache-2.0 | `91d76c158a1e…` |
| `official_moss_t2i_bboxes.json` | `official-layout` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_moss_image_t2i_bboxes.json) | MIT/Apache-2.0 | `225aa1c2831b…` |
| `matlowai_banding_fix_presets.json` | `matlowai-presets` | [link](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand/resolve/main/workflows/moss_banding_fix_presets.json) | Apache-2.0 | `7183c0d2f921…` |
| `matlowai_banding_fix_presets_rapid_moss.json` | `matlowai-presets-rapid` | [link](https://huggingface.co/MATLOWAI/MOSS-Image-SFT-DeBand/resolve/main/workflows/moss_banding_fix_presets_rapid_moss.json) | Apache-2.0 | `3e0fcab3e11c…` |
| `official_moss_base_t2i.json` | `official-base-t2i` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_moss_image_base_t2i.json) | MIT/Apache-2.0 | `79f897bcc9d8…` |

To add a workflow: put the upstream file here unchanged, add an entry to `../profile.yaml` with `file`,
`source`, `license`, `upstream_url`, `upstream_sha256`, `models` and `app`, then run
`python scripts/validate-profile.py examples/profiles/moss-image/`.
