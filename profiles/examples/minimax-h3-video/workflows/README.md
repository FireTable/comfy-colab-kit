# `minimax-h3-video` workflows

Unmodified upstream workflow files, redistributed under their original licenses (MIT templates).
Do not edit them by hand — App-mode metadata, `model_aliases`, 8-step `overrides`, and baked-TurboFusion LoRA bypasses are applied by `packages/colab/setup.py` at install time.

| File | Workflow id | Upstream | License | sha256 |
|---|---|---|---|---|
| `video_minimax_h3_r2v.json` | `h3-r2v` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_r2v.json) | MIT template; weights MiniMax H3 community license | `e083efe44268…` |
| `video_minimax_h3_i2v.json` | `h3-i2v` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_i2v.json) | MIT template; weights MiniMax H3 community license | `2a687889e1ab…` |
| `video_minimax_h3_i2v_continuation.json` | `h3-extend` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_i2v_continuation.json) | MIT template + Apache-2.0 Extender; weights MiniMax H3 / RelayVSR | `cbc5b948dc50…` |
| `video_minimax_h3_fun_controlnet_union.json` | `h3-controlnet` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_fun_controlnet_union.json) | MIT template; weights MiniMax H3 community license + MIT SDPose | `64aea27bc8f7…` |
| `video_minimax_h3_t2v.json` | `h3-t2v` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/video_minimax_h3_t2v.json) | MIT template; weights MiniMax H3 community license | `5bc82b5d3185…` |

To add a workflow: put the upstream file here unchanged, add an entry to `../profile.yaml` with `file`,
`source`, `license`, `upstream_url`, `upstream_sha256`, `models` and `app`, then run
`python3 scripts/validate-profile.py profiles/examples/minimax-h3-video/`.
