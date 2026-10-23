# `sd-latest` workflows

Unmodified upstream workflow files, redistributed under their original licenses (Apache-2.0 or MIT).
Do not edit them by hand — App-mode metadata and `overrides` are applied by `colab/setup.py` at install time.
URL-only workflows (licenses that do not allow or do not state redistribution) are **not** stored here.

| File | Workflow id | Upstream | License | sha256 |
|---|---|---|---|---|
| `official_sd3.5_simple.json` | `sd35-large` | [link](https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/sd3.5_simple_example.json) | MIT; model Stability Community License (gated) | `22636f8ea8d3…` |

To add a workflow: put the upstream file here unchanged, add an entry to `../profile.yaml` with `file`,
`source`, `license`, `upstream_url`, `upstream_sha256`, `models` and `app`, then run
`python scripts/validate-profile.py examples/profiles/sd-latest/`.
