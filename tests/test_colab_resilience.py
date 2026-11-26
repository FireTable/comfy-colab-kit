import asyncio
import copy
import json
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock

import jsonschema
import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from gate.app import SESSION, build_app
from gate.colab import (
    ColabCLI,
    ColabError,
    is_retryable_alloc_error,
    parse_session_details,
)
from gate.config import DEFAULT_CU_RATES, Settings
from gate.plan import build_fetch_plan, build_plan
from gate.profiles import ProfileError, load_profile, parse_profile
from gate.session import Session
from gate.state import State
from tests.fakes import FakeColab

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "packages/schemas/profile.schema.json"
SCHEMA = json.loads(SCHEMA_FILE.read_text())


async def wait_for(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise TimeoutError
        await asyncio.sleep(0.01)


def test_colab_cli_env_defaults_and_high_mem(monkeypatch):
    monkeypatch.delenv("REQUEST_TIMEOUT", raising=False)
    cli = ColabCLI()
    assert cli.env["REQUEST_TIMEOUT"] == "60"

    monkeypatch.setenv("REQUEST_TIMEOUT", "90")
    cli2 = ColabCLI()
    assert cli2.env["REQUEST_TIMEOUT"] == "90"


async def test_colab_cli_new_passes_high_mem_only_when_supported(monkeypatch):
    calls = []

    async def fake_run(self, *args, stdin=None, timeout=600):
        calls.append((args, timeout))
        return ""

    monkeypatch.setattr(ColabCLI, "_run", fake_run)
    cli = ColabCLI()

    await cli.new("comfy", "A100", high_mem=True, timeout=150)
    await cli.new("comfy", "T4", high_mem=True, timeout=120)
    await cli.new("comfy", "L4", high_mem=True, timeout=150)
    await cli.new("comfy", "G4", high_mem=True, timeout=150)
    await cli.new("comfy", "A100", high_mem=False, timeout=150)

    assert calls == [
        (("new", "-s", "comfy", "--gpu", "A100", "--high-mem"), 150),
        (("new", "-s", "comfy", "--gpu", "T4", "--high-mem"), 120),
        (("new", "-s", "comfy", "--gpu", "L4"), 150),
        (("new", "-s", "comfy", "--gpu", "G4"), 150),
        (("new", "-s", "comfy", "--gpu", "A100"), 150),
    ]


def test_parse_session_details_including_webui():
    out = (
        "[comfy] gpu-a100-s-abc | Hardware: NVIDIA A100-SXM4-80GB | Shape: High-RAM | Variant: GPU\n"
        "[?] gpu-l4-s-xyz | Hardware: NVIDIA L4 | Shape: Standard | Variant: GPU\n"
        "[blackwell] gpu-g4-s-999 | Hardware: NVIDIA RTX PRO 6000 | Shape: Standard | Variant: GPU\n"
    )
    parsed = parse_session_details(out)
    assert parsed["comfy"]["endpoint"] == "gpu-a100-s-abc"
    assert parsed["comfy"]["hardware"] == "NVIDIA A100-SXM4-80GB"
    assert parsed["comfy"]["shape"] == "High-RAM"
    assert parsed["comfy"]["variant"] == "GPU"
    assert parsed["comfy"]["accelerator"] == "A100"

    assert parsed["?"]["accelerator"] == "L4"
    assert parsed["blackwell"]["accelerator"] == "G4"


async def test_session_details_handles_no_active_session(monkeypatch):
    async def fake_run(self, *args, stdin=None, timeout=600):
        raise ColabError("colab sessions failed (1): No active sessions found on server.")

    monkeypatch.setattr(ColabCLI, "_run", fake_run)
    assert await ColabCLI().session_details() == {}


@pytest.mark.parametrize("err,expected", [
    (ColabError("colab new failed (1): HTTP 503 Service Unavailable"), True),
    ("No GPU capacity available right now", True),
    ("Resource unavailable; try again", True),
    ("colab new timed out", True),
    ("Request timeout waiting for assignment", True),
    ("quota temporarily unavailable", True),
    (asyncio.TimeoutError(), True),
    ("401 Unauthorized", False),
    ("403 Forbidden: quota exceeded", False),
    ("Please re-authenticate using colab login", False),
    ("OAuth token expired: invalid_grant", False),
    ("invalid accelerator BOGUS", False),
])
def test_is_retryable_alloc_error(err, expected):
    assert is_retryable_alloc_error(err) is expected


def test_config_resilience_defaults_and_cu_rates():
    s = Settings.from_env({"AUTH_DISABLED": "true"})
    assert "profiles/examples/minimax-h3-video" in s.profiles
    assert s.colab_fallback_accelerators == ()
    assert s.colab_alloc_retries == 2
    assert s.colab_alloc_timeout_s == 150
    assert s.colab_alloc_backoff_s == 15.0
    assert s.cu_rates_by_gpu == DEFAULT_CU_RATES
    assert s.cu_rate_for("T4") == 1.15
    assert s.cu_rate_for("L4") == 1.71
    assert s.cu_rate_for("A100", high_mem=False) == 5.4
    assert s.cu_rate_for("A100", high_mem=True) == 7.2
    assert s.cu_rate_for("G4") == 9.4
    assert s.cu_rate_for("H100") == 18.0

    s2 = Settings.from_env({
        "AUTH_DISABLED": "true",
        "CU_PER_HOUR": "1.90",
        "COLAB_FALLBACK_ACCELERATORS": "G4, L4",
        "COLAB_ALLOC_RETRIES": "3",
        "COLAB_ALLOC_TIMEOUT_S": "120",
        "COLAB_ALLOC_BACKOFF_S": "5.5",
    })
    assert s2.colab_fallback_accelerators == ("G4", "L4")
    assert s2.colab_alloc_retries == 3
    assert s2.colab_alloc_timeout_s == 120
    assert s2.colab_alloc_backoff_s == 5.5
    assert s2.cu_rate_for("L4") == 1.90


def _triprism_like_profile_dict():
    base = yaml.safe_load((ROOT / "tests/fixtures/profiles/tiny/profile.yaml").read_text())
    base["id"] = "triprism-test"
    base["accelerator"] = "A100"
    base["fallback_accelerators"] = ["G4", "L4"]
    base["high_mem"] = True
    base["model_aliases"] = {
        "upstream_dit.safetensors": "dit-triprism-int8",
    }
    base["model_aliases_by_gpu"] = {
        "L4": {
            "upstream_dit.safetensors": "dit-triprism-tiny",
            "triprism_int8.safetensors": "dit-triprism-tiny",
        }
    }
    base["models"] = [
        {
            "id": "dit-triprism-int8",
            "dest": "diffusion_models/triprism_int8.safetensors",
            "url": "hf://org/repo/triprism_int8.safetensors",
            "size_gb": 19.6,
            "license": "apache-2.0",
            "core": True,
        },
        {
            "id": "dit-triprism-tiny",
            "dest": "diffusion_models/triprism_tiny.safetensors",
            "url": "hf://org/repo/triprism_tiny.safetensors",
            "size_gb": 8.2,
            "license": "apache-2.0",
        },
        {
            "id": "latent-up",
            "dest": "latent_upscale_models/upscaler.safetensors",
            "url": "hf://org/repo/upscaler.safetensors",
            "size_gb": 0.8,
            "license": "apache-2.0",
        },
    ]
    base["workflows"][0]["models"] = ["dit-triprism-int8"]
    return base


def test_profile_schema_and_plan_gpu_alias_substitution():
    d = _triprism_like_profile_dict()
    jsonschema.validate(d, SCHEMA)
    p = parse_profile(d, ROOT)
    assert p.accelerator == "A100"
    assert p.fallback_accelerators == ("G4", "L4")
    assert p.high_mem is True
    assert p.summary()["fallback_accelerators"] == ["G4", "L4"]
    assert p.summary()["high_mem"] is True

    s = Settings.from_env({"AUTH_DISABLED": "true"})

    plan_a100 = build_plan(p, s, actual_accelerator="A100")
    assert [m["id"] for m in plan_a100["models"]] == ["dit-triprism-int8"]
    assert plan_a100["models"][0]["core"] is True
    assert plan_a100["model_aliases"]["upstream_dit.safetensors"] == "triprism_int8.safetensors"

    plan_l4 = build_plan(p, s, actual_accelerator="L4")
    l4_ids = [m["id"] for m in plan_l4["models"]]
    assert l4_ids == ["dit-triprism-tiny"]
    assert plan_l4["models"][0]["core"] is True
    assert "dit-triprism-int8" not in l4_ids
    assert plan_l4["model_aliases"]["upstream_dit.safetensors"] == "triprism_tiny.safetensors"

    fetch_l4 = build_fetch_plan(p, s, ["examples-img2img"], actual_accelerator="L4")
    assert [m["id"] for m in fetch_l4["models"]] == ["dit-triprism-tiny"]

    bad = copy.deepcopy(d)
    bad["model_aliases_by_gpu"]["L4"]["x.safetensors"] = "missing-model"
    with pytest.raises(ProfileError, match="unknown model"):
        parse_profile(bad, ROOT)


def _make_session(tmp_path, colab, profile_dict=None, **env):
    s = Settings.from_env({
        "AUTH_DISABLED": "true",
        "STATE_DIR": str(tmp_path),
        "COLAB_ALLOC_RETRIES": "2",
        "COLAB_ALLOC_BACKOFF_S": "0",
        **env,
    })
    p = parse_profile(profile_dict or _triprism_like_profile_dict(), ROOT)
    return Session(s, {p.id: p}, colab)


async def test_session_fallback_chain_and_l4_plan_and_cu(tmp_path):
    uploaded_plans = []

    class FallbackColab(FakeColab):
        async def new(self, name, accelerator, high_mem=False, timeout=180):
            self.calls.append(f"new:{name}:{accelerator}:hm={high_mem}")
            if accelerator in ("A100", "G4"):
                raise ColabError("colab new failed (1): HTTP 503 Service Unavailable")
            self.live.add(name)
            self.live_details[name] = {"hardware": accelerator, "accelerator": accelerator}

        async def upload(self, name, src, dst):
            self.calls.append("upload")
            with tarfile.open(src, "r:gz") as tar:
                uploaded_plans.append(json.loads(tar.extractfile("plan.json").read()))

    t = [1_000_000.0]
    colab = FallbackColab()
    sess = _make_session(tmp_path, colab)
    sess.clock = sess.machine.clock = lambda: t[0]
    sess._last_accrual = t[0]

    await sess.wake("triprism-test")
    await wait_for(lambda: sess.machine.state == State.READY)

    assert colab.calls.count("new:comfy:A100:hm=True") == 2
    assert colab.calls.count("new:comfy:G4:hm=False") == 2
    assert colab.calls.count("new:comfy:L4:hm=False") == 1
    assert sess.status()["accelerator"] == "L4"
    assert sess.status()["cu"]["per_hour"] == 1.71
    assert any("falling back to G4" in line for line in sess.bus.logs)
    assert any("falling back to L4" in line for line in sess.bus.logs)

    # Plan uploaded to VM must use L4-appropriate checkpoint only
    assert [m["id"] for m in uploaded_plans[0]["models"]] == ["dit-triprism-tiny"]
    assert uploaded_plans[0]["models"][0]["core"] is True

    # Verify accrual uses 1.71 for L4 and resets active_accelerator on stop
    t[0] += 3600
    await sess.stop("done")
    assert sum(sess.ledger.per_day.values()) == pytest.approx(1.71, rel=1e-3)
    assert sess.status()["accelerator"] == ""


async def test_session_a100_high_mem_cu_and_g4_override(tmp_path):
    t = [1_000_000.0]
    colab = FakeColab()
    sess = _make_session(tmp_path, colab)
    sess.clock = sess.machine.clock = lambda: t[0]
    sess._last_accrual = t[0]

    await sess.wake("triprism-test")
    await wait_for(lambda: sess.machine.state == State.READY)
    assert sess.status()["accelerator"] == "A100"
    assert sess.status()["cu"]["per_hour"] == 7.2
    assert colab.new_kwargs[0]["high_mem"] is True

    t[0] += 3600
    await sess.stop("done")
    assert sum(sess.ledger.per_day.values()) == pytest.approx(7.2, rel=1e-3)

    # Now force G4 via wake accelerator override
    await sess.wake("triprism-test", accelerator="G4")
    await wait_for(lambda: sess.machine.state == State.READY)
    assert sess.status()["accelerator"] == "G4"
    assert sess.status()["cu"]["per_hour"] == 9.4
    t[0] += 3600
    await sess.stop("done")
    assert sum(sess.ledger.per_day.values()) == pytest.approx(7.2 + 9.4, rel=1e-3)


async def test_allocation_reconciliation_adopts_vm_after_timeout(tmp_path):
    class TimeoutButLiveColab(FakeColab):
        async def new(self, name, accelerator, high_mem=False, timeout=180):
            self.calls.append(f"new:{name}:{accelerator}")
            self.live.add(name)
            self.live_details[name] = {"hardware": "NVIDIA A100-SXM4-80GB", "shape": "High-RAM", "accelerator": "A100"}
            raise ColabError("colab new timed out")

    colab = TimeoutButLiveColab()
    sess = _make_session(tmp_path, colab)
    await sess.wake("triprism-test")
    await wait_for(lambda: sess.machine.state == State.READY)
    assert [c for c in colab.calls if c.startswith("new:")] == ["new:comfy:A100"]
    assert sess.status()["accelerator"] == "A100"
    assert sess.status()["cu"]["per_hour"] == 7.2


async def test_fatal_auth_error_stops_immediately_without_retry_or_fallback(tmp_path):
    class AuthFailColab(FakeColab):
        async def new(self, name, accelerator, high_mem=False, timeout=180):
            self.calls.append(f"new:{name}:{accelerator}")
            raise ColabError("colab new failed (1): 401 Unauthorized: please re-authenticate")

    colab = AuthFailColab()
    sess = _make_session(tmp_path, colab)
    await sess.wake("triprism-test")
    await wait_for(lambda: sess.machine.state == State.FAILED)
    assert [c for c in colab.calls if c.startswith("new:")] == ["new:comfy:A100"]


async def test_wake_endpoint_accepts_and_validates_accelerator(tmp_path):
    s = Settings.from_env({"AUTH_DISABLED": "true", "STATE_DIR": str(tmp_path), "DEFAULT_PROFILE": "triprism-test"})
    p = parse_profile(_triprism_like_profile_dict(), ROOT)
    colab = FakeColab()
    app = build_app(s, colab, {p.id: p}, run_background=False)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        bad = await c.post("/_gate/wake", json={"profile": "triprism-test", "accelerator": "BOGUS"})
        assert bad.status == 400
        assert (await bad.json())["error"] == "invalid_accelerator"

        ok = await c.post("/_gate/wake", json={"profile": "triprism-test", "accelerator": "G4"})
        assert ok.status == 202
        sess = app[SESSION]
        await wait_for(lambda: sess.machine.state == State.READY)
        assert sess.status()["accelerator"] == "G4"
        assert sess.status()["cu"]["per_hour"] == 9.4
    finally:
        await c.close()
