# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first — it is the source of truth for this repository.

The six rules that matter most:

1. Never commit or print secrets; secrets are `*_FILE` paths. Run `bash scripts/check-secrets.sh` before committing.
2. Only `gate/session.py` (`Session.wake` / `Session.stop`) may start or stop Colab; one VM per gate, wakes join.
3. Identity comes from the verified Cloudflare Access JWT, never from a plain header.
4. Profiles reference community workflows and models by public URL with source + license; never author or vendor them.
5. Workflows stay untouched: never serialize `widgets` or inject artificial inputs into node JSON; App mode config lives strictly in `extra.linearData` (see `docs/APP_MODE.md` and `skills/comfy-workflow-ui/SKILL.md`).
6. `python -m pytest -q` must pass; keep schema, `gate/profiles.py`, docs and tests in sync.
