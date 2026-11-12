# `minimax-h3-image` workflows

Unmodified upstream workflow files, redistributed under their original licenses (MIT templates).
Do not edit them by hand — App-mode metadata and `model_aliases` are applied by `packages/colab/setup.py` at install time.

| File | Workflow id | Upstream | License | sha256 |
|---|---|---|---|---|
| `official_minimax_h3_image_t2i.json` | `h3-t2i` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_minimax_h3_image_t2i.json) | MIT template; weights MiniMax H3 Image community license | `73f7d9c137e4…` |
| `official_minimax_h3_image_edit.json` | `h3-edit` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_minimax_h3_image_edit.json) | MIT template; weights MiniMax H3 Image community license | `5b4330e9c8bc…` |

To add a workflow: put the upstream file here unchanged, add an entry to `../profile.yaml` with `file`,
`source`, `license`, `upstream_url`, `upstream_sha256`, `models` and `app`, then run
`python3 scripts/validate-profile.py profiles/examples/minimax-h3-image/`.
