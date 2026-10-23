import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from gate.config import Settings
from gate.plan import build_plan
from gate.profiles import ProfileError, hf_to_https, load_all, load_profile, parse_profile

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples/profiles").glob("*/profile.yaml"))
ALL = EXAMPLES + sorted((ROOT / "tests/fixtures/profiles").glob("*/profile.yaml"))
SCHEMA = json.loads((ROOT / "schemas/profile.schema.json").read_text())


FIXTURES = ROOT / "tests/fixtures/profiles"


def raw(name="moss-image"):
    base = FIXTURES if (FIXTURES / name).is_dir() else ROOT / "examples/profiles"
    return yaml.safe_load((base / name / "profile.yaml").read_text())


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.parent.name)
def test_examples_match_json_schema(path):
    jsonschema.validate(yaml.safe_load(path.read_text()), SCHEMA)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_examples_parse(path):
    p = load_profile(path.parent)
    assert p.app_default in p.workflows if p.workflows else p.app_default is None
    for w in p.workflows.values():
        assert w.license and w.app_inputs, w.id
    assert (path.parent / "README.md").is_file() and (path.parent / "workflows/README.md").is_file()


def test_three_example_profiles():
    assert sorted(p.parent.name for p in EXAMPLES) == ["moss-image", "qwen-image", "sd-latest"]


def test_moss_profile_contents():
    p = load_profile(ROOT / "examples/profiles/moss-image")
    assert p.app_default == "matlowai-presets"  # SFT bf16 + DeBand LoRAs: best quality on the shared base
    assert p.workflows["matlowai-deband-int8"].optional  # needs its own merged checkpoints -> opt-in
    assert {"akhaliq-4step", "rapid-moss-4step", "official-t2i", "official-layout", "matlowai-presets"} <= set(p.workflows)
    for wid in ("kijai-4step", "chinoll-edit-single", "chinoll-edit-multi", "matlowai-presets-rapid"):
        assert p.workflows[wid].optional, wid
    # link-only items (unclear / GPL licences) are never vendored
    for wid in ("kijai-4step", "chinoll-edit-single", "chinoll-edit-multi"):
        assert p.workflows[wid].url and not p.workflows[wid].file, wid
    assert p.workflows["matlowai-deband-int8"].file == "workflows/matlowai_deband_int8.json"
    assert "--preview-method" in p.comfyui_args
    assert p.comfyui_env["PYTORCH_CUDA_ALLOC_CONF"].startswith("expandable")
    assert isinstance(p.comfyui_settings, dict)
    # every model is pulled from its original public repo, never vendored
    assert all(m.url.startswith(("hf://", "https://")) for m in p.models.values())


def test_hf_url_conversion():
    assert hf_to_https("hf://a/b/x/y.safetensors") == "https://huggingface.co/a/b/resolve/main/x/y.safetensors"
    assert hf_to_https("hf://a/b@v1/y.json") == "https://huggingface.co/a/b/resolve/v1/y.json"


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d.update(hf_token="x"), "forbidden key"),
    (lambda d: d["models"][0].update(url="https://x.example.com/m.safetensors?token=abc"), "url"),
    (lambda d: d["models"][0].update(dest="../etc/passwd"), "dest"),
    (lambda d: d["models"][0].update(sha256="abc"), "sha256"),
    (lambda d: d["workflows"][0].update(models=["nope"]), "unknown model"),
    (lambda d: d["workflows"][0].update(custom_nodes=["nope"]), "unknown custom node"),
    (lambda d: d["workflows"][0].pop("license"), "license"),
    (lambda d: d.update(app_default="nope"), "app_default"),
    (lambda d: d["comfyui"].update(args=["--port", "9999"]), "managed by the kit"),
    (lambda d: d["comfyui"].update(args=["fast"]), "--flag"),
    (lambda d: d["comfyui"].update(env={"HF_TOKEN": "x"}), "forbidden key"),
    (lambda d: d["comfyui"].update(env={"lower": "x"}), "UPPER_CASE"),
    (lambda d: d["comfyui"].update(settings=["x"]), "mapping"),
    (lambda d: d["models"][0].update(license="hf_" + "a" * 30), "credential"),
])
def test_invalid_profiles(mutate, msg):
    d = copy.deepcopy(raw())
    mutate(d)
    with pytest.raises(ProfileError, match=msg):
        parse_profile(d, ROOT / "examples/profiles/moss-image")


def test_schema_rejects_secret_keys_too():
    d = copy.deepcopy(raw("tiny"))
    d["api_key"] = "x"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(d, SCHEMA)
    d = copy.deepcopy(raw("tiny"))
    d["comfyui"]["env"] = {"HF_TOKEN": "x"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(d, SCHEMA)


def test_load_all_collects_errors(tmp_path):
    ok, errors = load_all(["tests/fixtures/profiles/tiny", "examples/profiles/qwen-image", str(tmp_path / "missing")], ROOT)
    assert {"tiny", "qwen-image"} <= set(ok) and len(errors) == 1


def test_plan_contains_comfy_passthrough_and_dedups_models():
    p = load_profile(ROOT / "examples/profiles/moss-image")
    s = Settings.from_env({"AUTH_DISABLED": "true", "DEFAULT_VIEW": "graph"})
    plan = build_plan(p, s)
    assert plan["comfyui"]["args"] == list(p.comfyui_args)
    assert plan["comfyui"]["settings"] == p.comfyui_settings
    assert plan["default_view"] == "graph"
    ids = [m["id"] for m in plan["models"]]
    assert len(ids) == len(set(ids))
    assert all(m["url"].startswith("https://huggingface.co/") for m in plan["models"])
    assert not any(w["id"] == "kijai-4step" for w in plan["workflows"])  # optional by default
    core = {m["id"] for m in plan["models"] if m["core"]}
    assert {"sft-bf16", "te-int8", "vae", "deband-l37-41"} <= core
    assert all(m["core"] for m in plan["models"])  # nothing non-core is downloaded unless asked for
    assert "akhaliq-4step" in plan["on_demand"] and "akhaliq-4step-v2" in plan["on_demand"]["akhaliq-4step"]
    assert "chinoll-edit-patch" not in {m["id"] for m in plan["models"]}  # optional workflow not selected
    assert [n["id"] for n in plan["custom_nodes"]] == ["rapid-moss"]
    assert plan["model_aliases"]["qwen3.5_9b_w4a8_gptq.safetensors"] == "qwen3.5_9b_int8_convrot.safetensors"
    plan2 = build_plan(p, s, ["kijai-4step"])
    assert {w["id"] for w in plan2["workflows"]} == {"kijai-4step", "matlowai-presets"}


def test_workflow_file_and_loras(tmp_path):
    d = copy.deepcopy(raw("tiny"))
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows/x.json").write_text('{"nodes": []}')
    d["workflows"][0].pop("url")
    d["workflows"][0]["file"] = "workflows/x.json"
    d["workflows"][0]["loras"] = ["hf://owner/repo/style.safetensors"]
    jsonschema.validate(d, SCHEMA)
    p = parse_profile(d, tmp_path)
    w = p.workflows["examples-img2img"]
    assert w.file == "workflows/x.json" and w.loras == ("hf://owner/repo/style.safetensors",)
    plan = build_plan(p, Settings.from_env({"AUTH_DISABLED": "true"}))
    assert plan["workflows"][0]["inline"] == '{"nodes": []}'
    assert plan["workflows"][0]["loras"] == ["https://huggingface.co/owner/repo/resolve/main/style.safetensors"]
    d["workflows"][0]["file"] = "../x.json"
    with pytest.raises(ProfileError, match="under workflows/"):
        parse_profile(d, tmp_path)


def test_scaffold_profile_without_workflows_plans():
    d = copy.deepcopy(raw("tiny"))
    d["workflows"] = []
    d.pop("app_default", None)
    p = parse_profile(d, ROOT)
    assert p.workflows == {} and p.app_default is None
    plan = build_plan(p, Settings.from_env({"AUTH_DISABLED": "true"}))
    assert plan["workflows"] == []


def test_vendored_workflows_match_upstream_hash():
    import hashlib
    for path in EXAMPLES:
        for w in yaml.safe_load(path.read_text())["workflows"]:
            if "file" in w:
                data = (path.parent / w["file"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == w["upstream_sha256"], w["id"]
                assert w["license"].lower().startswith(("apache-2.0", "mit")), w["id"]


def test_moss_shares_one_base_and_text_encoder():
    p = load_profile(ROOT / "examples/profiles/moss-image")
    tes = {m for w in p.workflows.values() for m in w.models if p.models[m].dest.startswith("text_encoders/")}
    vaes = {m for w in p.workflows.values() for m in w.models if p.models[m].dest.startswith("vae/")}
    assert tes == {"te-int8"} and vaes == {"vae"}
    bases = {m for w in p.workflows.values() if not w.optional for m in w.models
             if p.models[m].dest.startswith("diffusion_models/")}
    assert bases == {"sft-bf16"}


def test_prefetch_and_requested_workflows_download_in_background():
    import dataclasses
    p = load_profile(ROOT / "examples/profiles/moss-image")
    s = Settings.from_env({"AUTH_DISABLED": "true"})
    wf = dict(p.workflows)
    wf["akhaliq-4step"] = dataclasses.replace(wf["akhaliq-4step"], prefetch=True)
    plan = build_plan(dataclasses.replace(p, workflows=wf), s)
    m = {x["id"]: x for x in plan["models"]}
    assert m["akhaliq-4step-v2"]["core"] is False and m["sft-bf16"]["core"] is True
    assert "akhaliq-4step" not in plan["on_demand"]


def test_fetch_plan_only_lists_requested_models():
    from gate.plan import build_fetch_plan
    p = load_profile(ROOT / "examples/profiles/moss-image")
    s = Settings.from_env({"AUTH_DISABLED": "true"})
    plan = build_fetch_plan(p, s, ["akhaliq-4step"])
    assert plan["fetch_only"] and plan["smoke"] is None
    assert {x["id"] for x in plan["models"]} == {"sft-bf16", "te-int8", "vae", "akhaliq-4step-v2"}
    assert not any(x["core"] for x in plan["models"])
    assert [w["id"] for w in plan["workflows"]] == ["akhaliq-4step"]
    with pytest.raises(ValueError):
        build_fetch_plan(p, s, ["nope"])
