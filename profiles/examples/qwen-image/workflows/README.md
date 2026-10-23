# `qwen-image` workflows

Unmodified upstream workflow files, redistributed under their original licenses (Apache-2.0 or MIT).
Do not edit them by hand — App-mode metadata and `overrides` are applied by `colab/setup.py` at install time.
URL-only workflows (licenses that do not allow or do not state redistribution) are **not** stored here.

| File | Workflow id | Upstream | License | sha256 |
|---|---|---|---|---|
| `official_image_qwen_image_2_1_turbo_t2i.json` | `qwen21-turbo-t2i` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_qwen_image_2_1_turbo_t2i.json) | MIT template; weights qwen-research license (check commercial) | `3eb9cef2866c…` |
| `official_image_qwen_image_2_1_t2i.json` | `qwen21-t2i` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_qwen_image_2_1_t2i.json) | MIT template; weights qwen-research license (check commercial) | `5f2b50eeaf27…` |
| `official_image_qwen_image_2_1_turbo_image_edit.json` | `qwen21-turbo-edit` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_qwen_image_2_1_turbo_image_edit.json) | MIT template; weights qwen-research license (check commercial) | `5a9622d55086…` |
| `official_image_qwen_image_2_1_image_edit.json` | `qwen21-edit` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_qwen_image_2_1_image_edit.json) | MIT template; weights qwen-research license (check commercial) | `c1e6551759de…` |

To add a workflow: put the upstream file here unchanged, add an entry to `../profile.yaml` with `file`,
`source`, `license`, `upstream_url`, `upstream_sha256`, `models` and `app`, then run
`python scripts/validate-profile.py examples/profiles/qwen-image/`.
