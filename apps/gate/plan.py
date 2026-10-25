"""Turn a validated Profile + Settings into the JSON plan executed by colab/setup.py."""
from __future__ import annotations

from .config import Settings
from .profiles import Profile, hf_to_https


def build_plan(profile: Profile, settings: Settings, workflow_ids: list[str] | None = None) -> dict:
    wf_ids = workflow_ids or [w.id for w in profile.workflows.values() if not w.optional]
    if profile.app_default and profile.app_default not in wf_ids:
        wf_ids = [profile.app_default, *wf_ids]
    unknown = [w for w in wf_ids if w not in profile.workflows]
    if unknown:
        raise ValueError(f"unknown workflows {unknown}")
    # Only the default workflow's (and the smoke test's) models are downloaded on wake, as core. Other installed
    # workflows' models are opt-in: explicitly requested workflows and `prefetch: true` ones download in the
    # background; everything else is fetched on demand (POST /_gate/fetch -> build_fetch_plan).
    requested = set(workflow_ids or [])
    eager = [w for w in wf_ids if w in requested or profile.workflows[w].prefetch]
    default_models = profile.models_for([profile.app_default]) if profile.app_default else []
    models = {m.id: m for m in default_models}
    for m in profile.models_for(eager) if eager else []:
        models.setdefault(m.id, m)
    node_ids: list[str] = []
    for wid in wf_ids:
        for n in profile.workflows[wid].custom_nodes:
            if n not in node_ids:
                node_ids.append(n)
    if settings.enable_smoke_test and profile.smoke:
        for mid in profile.smoke.models:
            models.setdefault(mid, profile.models[mid])
        for n in profile.smoke.custom_nodes:
            if n not in node_ids:
                node_ids.append(n)
    default_core = set(m.id for m in profile.models_for([profile.app_default])) if profile.app_default else set()
    return {
        "profile": profile.id,
        "comfyui": {
            "repo": (profile.raw.get("comfyui") or {}).get("repo", "https://github.com/comfyanonymous/ComfyUI"),
            "commit": profile.comfyui_commit,
            "frontend_package": profile.frontend_package,
            "args": list(profile.comfyui_args),
            "settings": profile.comfyui_settings,
            "env": profile.comfyui_env,
            "extra_model_paths": profile.extra_model_paths,
        },
        "models": [
            {"id": m.id, "dest": m.dest, "url": hf_to_https(m.url), "sha256": m.sha256,
             "size_gb": m.size_gb, "core": m.core or m.id in default_core}
            for m in models.values()
        ],
        "custom_nodes": [
            {"id": n, "url": profile.custom_nodes[n].url, "ref": profile.custom_nodes[n].ref,
             "subdir": profile.custom_nodes[n].subdir}
            for n in node_ids
        ],
        "workflows": [
            {"id": w.id, "title": w.title, "url": hf_to_https(w.url) if w.url else None,
             "inline": (profile.path / w.file).read_text() if w.file else None, "format": w.format,
             "loras": [hf_to_https(u) for u in w.loras],
             "app_inputs": [list(x) for x in w.app_inputs], "app_outputs": list(w.app_outputs),
             "overrides": list(w.overrides), "default": w.id == profile.app_default}
            for w in (profile.workflows[i] for i in wf_ids)
        ],
        "on_demand": {w: [m.id for m in profile.models_for([w]) if m.id not in models]
                      for w in wf_ids if any(m.id not in models for m in profile.models_for([w]))},
        "smoke": ({"api_url": hf_to_https(profile.smoke.api_url), "timeout_s": profile.smoke.timeout_s}
                  if settings.enable_smoke_test and profile.smoke else None),
        "model_aliases": {f: profile.models[mid].dest.rsplit("/", 1)[-1]
                          for f, mid in profile.model_aliases.items()},
        "default_view": settings.default_view,
        "fast_start": settings.fast_start,
        "download_concurrency": settings.download_concurrency,
        "download_connections": settings.download_connections,
        "model_store": settings.model_store,
        "xet_high_performance": settings.xet_high_performance,
        "pip_installer": settings.pip_installer,
        "watchdog_gate_lost_min": settings.auto_stop.watchdog_gate_lost_min,
        "max_session_hours": settings.auto_stop.max_session_hours
        + settings.auto_stop.extend_max_times * settings.auto_stop.extend_hours,
    }


def build_fetch_plan(profile: Profile, settings: Settings, workflow_ids: list[str]) -> dict:
    """Plan for an on-demand download on a running VM: the listed workflows' models (+ their JSON/nodes).
    setup.py skips files that already exist, so asking twice is cheap."""
    unknown = [w for w in workflow_ids if w not in profile.workflows]
    if unknown or not workflow_ids:
        raise ValueError(f"unknown workflows {unknown}")
    full = build_plan(profile, settings, list(workflow_ids))
    wanted = {m.id for m in profile.models_for(list(workflow_ids))}
    full["models"] = [{**m, "core": False} for m in full["models"] if m["id"] in wanted]
    full["workflows"] = [w for w in full["workflows"] if w["id"] in workflow_ids]
    full["fetch_only"] = True
    full["smoke"] = None
    return full
