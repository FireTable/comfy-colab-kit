# Contributing to Comfy Colab Kit

Thank you for your interest in contributing to **Comfy Colab Kit**! Whether you want to share a new model profile, report a bug, or improve the gateway, this guide covers the repository rules and fast paths.

---

## 1. Contributing a New Model Profile (Fastest Path via AI Agent)

You can scaffold and validate a new community profile (e.g., Flux.1, Wan 2.1, HunyuanVideo, SDXL) in minutes using an AI coding agent:

1. Read [**`docs/AGENT_SCAFFOLDING.md`**](docs/AGENT_SCAFFOLDING.md) and [**`docs/PROFILES.md`**](docs/PROFILES.md).
2. Create your profile under `profiles/examples/<profile-id>/profile.yaml`.
3. Follow our **strict licensing & security rules**:
   - **Never author original workflows**: Only reference community-published or official workflows with `source` and `license` recorded.
   - **Vendor only MIT / Apache-2.0 workflows**: Place the unmodified workflow JSON under `workflows/` with `upstream_url` and `upstream_sha256` only when its license is MIT or Apache-2.0. Reference all other workflows via `url:`.
   - **Never commit model weights or secrets**: Weights must be referenced via public `hf://` or `https://` URLs without query strings.
   - **Wire ComfyUI App Mode**: Configure `app.inputs` and `app.outputs` following [**`docs/APP_MODE.md`**](docs/APP_MODE.md).
4. Validate your profile before opening a Pull Request:
   ```bash
   python3 scripts/validate-profile.py
   bash scripts/check-secrets.sh
   python3 -m pytest -q
   ```

---

## 2. Adapting a Fork for Other Google Colab GPU Workloads

If you forked this repository to run another service on Google Colab (such as vLLM, Ollama, SD-WebUI Forge, Fooocus, or a custom Gradio/FastAPI server), see [**Part 2 of `docs/AGENT_SCAFFOLDING.md`**](docs/AGENT_SCAFFOLDING.md#part-2--forking-to-run-any-colab-gpu-workload-beyond-comfyui) for the 4 architectural seams. Generic gateway improvements that keep the core lightweight are always welcome upstream!

---

## 3. Code & Pull Request Rules

1. **Zero secrets in git**: Never commit tokens, keys, cookies, real email addresses, or private hostnames. Always run `bash scripts/check-secrets.sh` before committing.
2. **Architecture SVG diagrams**: Never hand-edit `assets/architecture/flow*.svg` or `hero.svg`. Edit `assets/architecture/flow.spec.json` or `scripts/build-architecture-svg.py` and re-run the generator.
3. **Commit style**: Use English Conventional Commits (`<type>(<scope>): <imperative summary>`, e.g., `feat(profiles): add flux-schnell profile`).
4. **Tests**: Ensure `python3 -m pytest -q` passes with zero errors or warnings.
