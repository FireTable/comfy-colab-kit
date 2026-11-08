# Custom Profiles (`profiles/custom/`)

Place your custom model profiles in this directory.

## How to add a custom profile

1. Create a subdirectory here, e.g. `profiles/custom/my-model/`.
2. Add a `profile.yaml` following the schema in `packages/schemas/profile.schema.json`.
3. Add any custom workflow JSON files in `profiles/custom/my-model/workflows/`.
4. Register your custom profile path in your `.env` file:
   ```bash
   PROFILES="profiles/examples/moss-image,profiles/examples/flux-klein,profiles/custom/my-model"
   ```
5. When `comfy-gate` is running, custom profiles are re-read dynamically on wake without restarting the gateway.

For details on scaffolding new profiles, see [`docs/AGENT_SCAFFOLDING.md`](../../docs/AGENT_SCAFFOLDING.md) and [`docs/PROFILES.md`](../../docs/PROFILES.md).
