"""Tests for durable job queue, input rehydration, crash recovery, Range streaming, and cold DB restore."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gate.app import JOBS, SESSION, build_app
from gate.config import Settings
from gate.profiles import load_profile
from gate.session import redact_secrets
from gate.state import State
from tests.fake_gdrive import FakeGDriveServer
from tests.fakes import FakeColab

ROOT = Path(__file__).resolve().parent.parent

# Minimal valid 1x1 PNG bytes
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x02\x00"
    b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeComfyBackend:
    """In-memory fake ComfyUI server simulating /upload/image, /prompt, /queue, /history, /view."""

    def __init__(self, output_bytes: bytes | None = None):
        self.uploaded_files: dict[str, bytes] = {}
        self.root_queries: list[str] = []
        self.submitted_prompts: list[dict[str, Any]] = []
        self.active_prompts: set[str] = set()
        self.max_concurrent_prompts: int = 0
        self.history: dict[str, dict[str, Any]] = {}
        self.outputs: dict[str, bytes] = {}
        self.default_output_bytes = output_bytes if output_bytes is not None else (TINY_PNG + b"PAD" * 2048)
        self.process_delay: float = 0.05
        self.hold_event: asyncio.Event | None = None
        self.fail_next_error: dict[str, str] | None = None
        self.reject_next_400: str | None = None
        self.rename_uploads: dict[str, str] = {}
        self.require_input_uploaded: bool = True
        self._tasks: list[asyncio.Task] = []
        self._server: TestServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.port}"

    def crash_wipe(self) -> None:
        """Simulate a fresh VM after crash: wipe all uploaded inputs, queue, and history."""
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        self.uploaded_files.clear()
        self.active_prompts.clear()
        self.history.clear()
        self.outputs.clear()

    async def start(self) -> "FakeComfyBackend":
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_get("/", self._root_html)
        app.router.add_get("/system_stats", self._system_stats)
        app.router.add_post("/kit/heartbeat", self._heartbeat)
        app.router.add_post("/upload/image", self._upload)
        app.router.add_post("/upload/mask", self._upload)
        app.router.add_post("/api/upload/image", self._upload)
        app.router.add_post("/api/upload/mask", self._upload)
        app.router.add_post("/prompt", self._prompt)
        app.router.add_post("/api/prompt", self._prompt)
        app.router.add_get("/queue", self._queue)
        app.router.add_get("/history/{prompt_id}", self._history_one)
        app.router.add_get("/history", self._history_all)
        app.router.add_get("/api/history/{prompt_id}", self._history_one)
        app.router.add_get("/api/history", self._history_all)
        app.router.add_get("/view", self._view)
        app.router.add_post("/interrupt", self._interrupt)
        self._server = TestServer(app)
        await self._server.start_server()
        return self

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._server:
            await self._server.close()

    async def __aenter__(self) -> "FakeComfyBackend":
        return await self.start()

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _root_html(self, req: web.Request) -> web.Response:
        self.root_queries.append(req.query_string)
        return web.Response(
            text="<html><head><title>ComfyUI Mock</title></head><body><div>ComfyUI</div></body></html>",
            content_type="text/html",
            headers={"X-Frame-Options": "DENY"},
        )

    async def _system_stats(self, req: web.Request) -> web.Response:
        return web.json_response({"system": {"os": "linux"}, "devices": [{"name": "NVIDIA L4"}]})

    async def _heartbeat(self, req: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _interrupt(self, req: web.Request) -> web.Response:
        self.active_prompts.clear()
        return web.json_response({"ok": True})

    async def _upload(self, req: web.Request) -> web.Response:
        reader = await req.multipart()
        fname = "upload.png"
        data = b""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name in ("image", "file") or part.filename:
                fname = Path(part.filename or "upload.png").name
                data = await part.read(decode=False)
        out_name = self.rename_uploads.get(fname, fname)
        self.uploaded_files[out_name] = data
        return web.json_response({"name": out_name, "subfolder": "", "type": "input"})

    async def _prompt(self, req: web.Request) -> web.Response:
        body = await req.json()
        if self.reject_next_400:
            msg = self.reject_next_400
            self.reject_next_400 = None
            return web.json_response({"error": {"message": msg}, "node_errors": {}}, status=400)

        pid = str(body.get("prompt_id") or uuid.uuid4())
        try:
            canonical = str(uuid.UUID(pid))
        except ValueError:
            canonical = ""
        if not canonical or canonical != pid:
            return web.json_response(
                {
                    "error": {
                        "type": "invalid_prompt_id",
                        "message": "prompt_id must be a valid UUID",
                        "details": "prompt_id must be a UUID string in canonical lowercase hyphenated form",
                    },
                    "node_errors": {},
                },
                status=400,
            )
        prompt = body.get("prompt") or {}

        # Verify LoadImage inputs are actually present on this VM!
        if self.require_input_uploaded:
            for node in prompt.values():
                if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                    img_name = (node.get("inputs") or {}).get("image")
                    if img_name and img_name not in self.uploaded_files:
                        return web.json_response(
                            {"error": {"message": f"missing input image {img_name}"}, "node_errors": {}},
                            status=400,
                        )

        self.submitted_prompts.append({"prompt_id": pid, "prompt": prompt, "extra_data": body.get("extra_data")})
        self.active_prompts.add(pid)
        self.max_concurrent_prompts = max(self.max_concurrent_prompts, len(self.active_prompts))

        async def _finish() -> None:
            try:
                if self.hold_event is not None:
                    await self.hold_event.wait()
                await asyncio.sleep(self.process_delay)
                self.active_prompts.discard(pid)
                if self.fail_next_error:
                    err_info = self.fail_next_error
                    self.fail_next_error = None
                    self.history[pid] = {
                        "prompt": [0, pid, prompt, {}, []],
                        "outputs": {},
                        "status": {
                            "status_str": "error",
                            "completed": False,
                            "messages": [
                                ["execution_start", {"prompt_id": pid}],
                                [
                                    "execution_error",
                                    {
                                        "prompt_id": pid,
                                        "node_id": err_info.get("node_id", "10"),
                                        "exception_message": err_info.get("message", "CUDA out of memory"),
                                    },
                                ],
                            ],
                        },
                    }
                    return

                out_name = f"out_{pid[:8]}.png"
                self.outputs[out_name] = self.default_output_bytes
                self.history[pid] = {
                    "prompt": [0, pid, prompt, {}, []],
                    "outputs": {
                        "9": {
                            "images": [{"filename": out_name, "subfolder": "", "type": "output"}],
                        }
                    },
                    "status": {"status_str": "success", "completed": True, "messages": []},
                }
            except asyncio.CancelledError:
                self.active_prompts.discard(pid)

        self._tasks.append(asyncio.create_task(_finish()))
        return web.json_response({"prompt_id": pid, "number": len(self.submitted_prompts), "node_errors": {}})

    async def _queue(self, req: web.Request) -> web.Response:
        return web.json_response({
            "queue_running": [[0, pid] for pid in sorted(self.active_prompts)],
            "queue_pending": [],
        })

    async def _history_one(self, req: web.Request) -> web.Response:
        pid = req.match_info["prompt_id"]
        if pid in self.history:
            return web.json_response({pid: self.history[pid]})
        return web.json_response({})

    async def _history_all(self, req: web.Request) -> web.Response:
        return web.json_response(self.history)

    async def _view(self, req: web.Request) -> web.Response:
        fn = req.query.get("filename", "")
        if fn not in self.outputs:
            return web.Response(status=404, text="not found")
        return web.Response(body=self.outputs[fn], content_type="image/png")


async def _wait_for(pred, timeout: float = 5.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise TimeoutError("timed out waiting for predicate")
        await asyncio.sleep(0.02)


def _sample_prompt(text: str = "a sunlit anime cat", seed: int = 42, image_name: str = "") -> dict[str, Any]:
    g: dict[str, Any] = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": text}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst quality, bad anatomy"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "positive": ["6", 0], "negative": ["7", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "test", "images": ["3", 0]}},
    }
    if image_name:
        g["10"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    return g


def _make_gate_client(
    tmp_path: Path,
    backend_url: str,
    oauth_file: Path | None = None,
    colab: FakeColab | None = None,
    **env_overrides: str,
) -> tuple[web.Application, TestClient]:
    state_dir = tmp_path / "state"
    env = {
        "AUTH_DISABLED": "true",
        "STATE_DIR": str(state_dir),
        "ARCHIVE_DIR": str(tmp_path / "archive"),
        "THUMBS_DIR": str(tmp_path / "thumbs"),
        "DEFAULT_PROFILE": "tiny",
        "COLAB_TUNNEL_HOST": backend_url,
        "DB_BACKUP_DEBOUNCE_S": "0.05",
        "QUEUE_MAX_RETRIES": "2",
        "AGENT_TOKEN_FILE": "",
        **env_overrides,
    }
    if oauth_file is not None:
        env["GDRIVE_OAUTH_FILE"] = str(oauth_file)
    s = Settings.from_env(env)
    prof = {
        "tiny": load_profile(ROOT / "tests/fixtures/profiles/tiny"),
        "moss-image": load_profile(ROOT / "profiles/examples/moss-image"),
    }
    app = build_app(s, colab or FakeColab(new_delay=0.01), prof, run_background=False, run_dispatcher=True)
    app[JOBS].poll_interval_s = 0.03
    client = TestClient(TestServer(app))
    return app, client


# ── Unit tests for config & secret redaction ──────────────────────────────────


def test_config_queue_and_gdrive_defaults(tmp_path: Path) -> None:
    s = Settings.from_env({
        "AUTH_DISABLED": "true",
        "STATE_DIR": str(tmp_path / "state"),
    })
    assert s.gdrive_oauth_file == ""
    assert s.gdrive_root_folder_id == ""
    assert s.archive_dir == str(tmp_path / "archive")
    assert s.thumbs_dir == str(tmp_path / "thumbs")
    assert s.archive_max_gb == 20.0
    assert s.queue_intercept_enabled is True
    assert s.queue_auto_retry is True
    assert s.queue_max_retries == 2
    assert s.db_backup_debounce_s == 15.0


def test_redact_secrets_strips_google_oauth_tokens() -> None:
    access = "ya29." + "a" * 24
    refresh = "1//04" + "b" * 30
    line = f"err token={access} and refresh={refresh}"
    cleaned = redact_secrets(line)
    assert "ya29." not in cleaned
    assert "1//04" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned


# ── 1. Offline queue + upload while sleeping -> wake -> archive to GDrive ────


@pytest.mark.asyncio
async def test_offline_queue_and_upload_executes_and_archives_to_gdrive(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gd") as gdrive, FakeComfyBackend() as comfy:
        oauth_file = gdrive.write_oauth_file(tmp_path / "oauth.json")
        app, client = _make_gate_client(tmp_path, comfy.base_url, oauth_file=oauth_file)
        await client.start_server()
        try:
            sess = app[SESSION]
            assert sess.machine.state == State.SLEEPING

            # Upload input image while GPU is sleeping
            form = aiohttp.FormData()
            form.add_field("image", TINY_PNG, filename="portrait.png", content_type="image/png")
            up_r = await client.post("/_gate/jobs/upload", data=form)
            assert up_r.status == 201
            up_data = await up_r.json()
            assert up_data["filename"] == "portrait.png"
            assert up_data["storage_backend"] == "gdrive"

            # Enqueue job while GPU is sleeping
            job_r = await client.post("/_gate/jobs", json={
                "profile": "tiny",
                "prompt": _sample_prompt("cyberpunk portrait", seed=777, image_name="portrait.png"),
            })
            assert job_r.status == 202
            job = await job_r.json()
            assert job["status"] == "queued"
            assert job["prompt_text"] == "cyberpunk portrait"
            assert job["seed"] == 777
            assert len(job["inputs"]) == 1
            assert job["inputs"][0]["filename"] == "portrait.png"

            # Status shows 1 queued job
            st_r = await client.get("/_gate/status")
            st_data = await st_r.json()
            assert st_data["queue"]["queued"] == 1

            # Wake GPU -> FakeColab goes READY -> dispatcher rehydrates input + runs + archives
            wk_r = await client.post("/_gate/wake", json={"profile": "tiny"})
            assert wk_r.status == 202

            async def _done() -> bool:
                r = await client.get(f"/_gate/jobs/{job['id']}")
                d = await r.json()
                return d["status"] == "completed"

            for _ in range(150):
                if await _done():
                    break
                await asyncio.sleep(0.05)

            final_r = await client.get(f"/_gate/jobs/{job['id']}")
            final_job = await final_r.json()
            assert final_job["status"] == "completed"
            # Verify prompt_id sent to ComfyUI is canonical lowercase hyphenated UUID
            assert len(comfy.submitted_prompts) == 1
            sent_pid = comfy.submitted_prompts[0]["prompt_id"]
            assert str(uuid.UUID(sent_pid)) == sent_pid
            assert final_job["comfy_prompt_id"] == sent_pid
            assert "portrait.png" in comfy.uploaded_files
            assert comfy.uploaded_files["portrait.png"] == TINY_PNG
            assert len(final_job["outputs"]) == 1
            out = final_job["outputs"][0]
            assert out["storage_backend"] == "gdrive"
            assert out["remote_id"] != ""
            assert out["thumb_url"].startswith("/_gate/archive/thumbs/")

            # Verify thumbnail endpoint serves valid bytes
            th_r = await client.get(out["thumb_url"])
            assert th_r.status == 200
            assert len(await th_r.read()) > 0

            # Verify job.json manifest was written to FakeGDriveServer
            gd_names = [it.get("name") for it in gdrive._files.values() if not it.get("trashed")]
            assert "job.json" in gd_names
        finally:
            await client.close()


# ── 2. Serial 1-in-flight backpressure ───────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_one_in_flight_backpressure(tmp_path: Path) -> None:
    async with FakeComfyBackend() as comfy:
        comfy.process_delay = 0.12
        app, client = _make_gate_client(tmp_path, comfy.base_url)
        await client.start_server()
        try:
            await client.post("/_gate/wake", json={"profile": "tiny"})
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            r1 = await client.post("/_gate/jobs", json={"prompt": _sample_prompt("first prompt", seed=1)})
            r2 = await client.post("/_gate/jobs", json={"prompt": _sample_prompt("second prompt", seed=2)})
            assert r1.status == 202 and r2.status == 202
            j1, j2 = await r1.json(), await r2.json()

            for _ in range(150):
                d1 = await (await client.get(f"/_gate/jobs/{j1['id']}")).json()
                d2 = await (await client.get(f"/_gate/jobs/{j2['id']}")).json()
                if d1["status"] == "completed" and d2["status"] == "completed":
                    break
                await asyncio.sleep(0.05)

            d1 = await (await client.get(f"/_gate/jobs/{j1['id']}")).json()
            d2 = await (await client.get(f"/_gate/jobs/{j2['id']}")).json()
            assert d1["status"] == "completed"
            assert d2["status"] == "completed"
            assert comfy.max_concurrent_prompts == 1
            assert len(comfy.submitted_prompts) == 2
        finally:
            await client.close()


# ── 3. Crash recovery + input rehydration on fresh VM ────────────────────────


@pytest.mark.asyncio
async def test_crash_recovery_rehydrates_inputs_and_retries(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gd") as gdrive, FakeComfyBackend() as comfy:
        oauth_file = gdrive.write_oauth_file(tmp_path / "oauth.json")
        hold = asyncio.Event()
        comfy.hold_event = hold
        app, client = _make_gate_client(tmp_path, comfy.base_url, oauth_file=oauth_file)
        await client.start_server()
        try:
            await client.post("/_gate/wake", json={"profile": "tiny"})
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            # Upload input through intercepted /upload/image with a ComfyUI rename
            comfy.rename_uploads["ref.png"] = "ref (1).png"
            form = aiohttp.FormData()
            form.add_field("image", TINY_PNG, filename="ref.png", content_type="image/png")
            up_r = await client.post("/upload/image", data=form)
            assert up_r.status == 200
            up_json = await up_r.json()
            assert up_json["name"] == "ref (1).png"

            # Submit prompt through intercepted /prompt referencing renamed input
            pr_r = await client.post("/prompt", json={"prompt": _sample_prompt("crash recovery test", seed=99, image_name="ref (1).png")})
            assert pr_r.status == 200
            pr_json = await pr_r.json()
            job_id = pr_json["job_id"]

            # Wait until ComfyUI has received the first submission and job is running
            await _wait_for(lambda: len(comfy.submitted_prompts) == 1)

            # Simulate VM crash: stop session + wipe FakeComfyBackend memory (inputs & history gone)
            await app[SESSION].stop("simulated VM crash")
            comfy.crash_wipe()
            comfy.hold_event = None
            comfy.process_delay = 0.03

            mid = await (await client.get(f"/_gate/jobs/{job_id}")).json()
            assert mid["status"] == "interrupted"
            assert "ref (1).png" not in comfy.uploaded_files

            # Wake a fresh VM -> dispatcher re-uploads "ref (1).png" and retries job to completion!
            await client.post("/_gate/wake", json={"profile": "tiny"})
            for _ in range(150):
                cur = await (await client.get(f"/_gate/jobs/{job_id}")).json()
                if cur["status"] == "completed":
                    break
                await asyncio.sleep(0.05)

            final = await (await client.get(f"/_gate/jobs/{job_id}")).json()
            assert final["status"] == "completed"
            assert final["retries"] == 1
            assert "ref (1).png" in comfy.uploaded_files
            assert comfy.uploaded_files["ref (1).png"] == TINY_PNG
            assert len(comfy.submitted_prompts) == 2
            assert len(final["outputs"]) == 1
        finally:
            hold.set()
            await client.close()


# ── 4. Non-retryable node error / OOM ────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_retryable_node_error_marks_failed_without_retry(tmp_path: Path) -> None:
    async with FakeComfyBackend() as comfy:
        comfy.fail_next_error = {"node_id": "24", "message": "CUDA out of memory"}
        app, client = _make_gate_client(tmp_path, comfy.base_url)
        await client.start_server()
        try:
            await client.post("/_gate/wake", json={"profile": "tiny"})
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            r = await client.post("/_gate/jobs", json={"prompt": _sample_prompt("oom prompt", seed=5)})
            job_id = (await r.json())["id"]

            for _ in range(100):
                cur = await (await client.get(f"/_gate/jobs/{job_id}")).json()
                if cur["status"] == "failed":
                    break
                await asyncio.sleep(0.03)

            await asyncio.sleep(0.15)  # Ensure dispatcher does NOT retry it
            cur = await (await client.get(f"/_gate/jobs/{job_id}")).json()
            assert cur["status"] == "failed"
            assert cur["error_node"] == "24"
            assert "CUDA out of memory" in cur["error_message"]
            assert cur["retries"] == 0
            assert len(comfy.submitted_prompts) == 1
        finally:
            await client.close()


# ── 5. Range 206 streaming while GPU is sleeping + job controls ──────────────


@pytest.mark.asyncio
async def test_range_206_streaming_while_sleeping_and_job_controls(tmp_path: Path) -> None:
    big_payload = TINY_PNG + bytes(i % 251 for i in range(6000))
    async with FakeGDriveServer(tmp_path / "gd") as gdrive, FakeComfyBackend(output_bytes=big_payload) as comfy:
        oauth_file = gdrive.write_oauth_file(tmp_path / "oauth.json")
        app, client = _make_gate_client(tmp_path, comfy.base_url, oauth_file=oauth_file)
        await client.start_server()
        try:
            await client.post("/_gate/wake", json={"profile": "tiny"})
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            r = await client.post("/_gate/jobs", json={"prompt": _sample_prompt("range test", seed=123)})
            job_id = (await r.json())["id"]

            for _ in range(120):
                cur = await (await client.get(f"/_gate/jobs/{job_id}")).json()
                if cur["status"] == "completed":
                    break
                await asyncio.sleep(0.04)

            done = await (await client.get(f"/_gate/jobs/{job_id}")).json()
            assert done["status"] == "completed"
            asset = done["outputs"][0]

            # Stop GPU -> sleeping
            await client.post("/_gate/stop")
            assert app[SESSION].machine.state == State.SLEEPING

            # Range 206 byte streaming works while GPU is sleeping
            rng_r = await client.get(asset["url"], headers={"Range": "bytes=100-499"})
            assert rng_r.status == 206
            assert rng_r.headers["Content-Range"] == f"bytes 100-499/{len(big_payload)}"
            assert await rng_r.read() == big_payload[100:500]

            # ?download=1 adds Content-Disposition
            dl_r = await client.get(f"{asset['url']}?download=1")
            assert dl_r.status == 200
            assert "attachment" in dl_r.headers.get("Content-Disposition", "")
            assert await dl_r.read() == big_payload

            # Pin toggle & archive profiles & search
            pin_r = await client.post(f"/_gate/jobs/{job_id}/pin", json={"pinned": True})
            assert pin_r.status == 200
            assert (await pin_r.json())["pinned"] is True

            prof_r = await client.get("/_gate/archive/profiles")
            assert prof_r.status == 200
            prof_data = await prof_r.json()
            assert prof_data["profiles"][0]["profile_id"] == "tiny"
            assert prof_data["profiles"][0]["pinned"] == 1

            list_r = await client.get("/_gate/jobs?pinned=1&q=range")
            assert list_r.status == 200
            assert len((await list_r.json())["jobs"]) == 1

            # Delete job with delete_files=1 removes blob from FakeGDriveServer
            del_r = await client.delete(f"/_gate/jobs/{job_id}?delete_files=1")
            assert del_r.status == 200
            assert (await client.get(f"/_gate/jobs/{job_id}")).status == 404
            assert (await client.get(asset["url"])).status == 404

            # While sleeping (with machine.profile still set to 'tiny'), enqueueing with
            # "profile_id": "moss-image" is honored AND visible in /_gate/status queue summary.
            assert app[SESSION].machine.profile == "tiny"
            off_r = await client.post("/_gate/jobs", json={
                "profile_id": "moss-image",
                "prompt": _sample_prompt("offline other profile", seed=321),
            })
            assert off_r.status == 202
            off_job = await off_r.json()
            assert off_job["profile_id"] == "moss-image"
            st_after = await (await client.get("/_gate/status")).json()
            assert st_after["queue"]["queued"] == 1
        finally:
            await client.close()


# ── 6. Cold DB restore from FakeGDriveServer ─────────────────────────────────


@pytest.mark.asyncio
async def test_cold_db_restore_from_gdrive(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gd") as gdrive, FakeComfyBackend() as comfy:
        oauth_file = gdrive.write_oauth_file(tmp_path / "oauth.json")
        app1, client1 = _make_gate_client(tmp_path, comfy.base_url, oauth_file=oauth_file)
        await client1.start_server()
        job_id = ""
        asset_url = ""
        try:
            await client1.post("/_gate/wake", json={"profile": "tiny"})
            await _wait_for(lambda: app1[SESSION].machine.state == State.READY)
            r = await client1.post("/_gate/jobs", json={"prompt": _sample_prompt("cold restore gem", seed=888)})
            job_id = (await r.json())["id"]

            for _ in range(120):
                cur = await (await client1.get(f"/_gate/jobs/{job_id}")).json()
                if cur["status"] == "completed":
                    break
                await asyncio.sleep(0.04)

            cur = await (await client1.get(f"/_gate/jobs/{job_id}")).json()
            assert cur["status"] == "completed"
            asset_url = cur["outputs"][0]["url"]

            # Trigger explicit backup (and cleanup also backs up)
            bk = await client1.post("/_gate/storage/backup")
            assert bk.status == 200
            assert (await bk.json())["ok"] is True
        finally:
            await client1.close()

        # Delete local gate.db (+ WAL/SHM) to simulate blank disk / cold container restart
        state_dir = tmp_path / "state"
        for suffix in ("gate.db", "gate.db-wal", "gate.db-shm", "session.json"):
            (state_dir / suffix).unlink(missing_ok=True)
        assert not (state_dir / "gate.db").exists()

        # Start fresh app against the same FakeGDriveServer
        app2, client2 = _make_gate_client(tmp_path, comfy.base_url, oauth_file=oauth_file)
        await client2.start_server()
        try:
            assert (state_dir / "gate.db").is_file()
            restored = await (await client2.get(f"/_gate/jobs/{job_id}")).json()
            assert restored["id"] == job_id
            assert restored["status"] == "completed"
            assert restored["prompt_text"] == "cold restore gem"
            assert restored["seed"] == 888
            assert len(restored["outputs"]) == 1

            # Asset stream also still works on the cold-restored instance while sleeping
            asset_r = await client2.get(asset_url)
            assert asset_r.status == 200
            assert await asset_r.read() == comfy.default_output_bytes
        finally:
            await client2.close()


# ── Cancel, Retry, History fallback, and MCP integration ─────────────────────


@pytest.mark.asyncio
async def test_cancel_retry_history_fallback_and_mcp_queue(tmp_path: Path) -> None:
    async with FakeComfyBackend() as comfy:
        app, client = _make_gate_client(tmp_path, comfy.base_url)
        await client.start_server()
        try:
            # 1. Cancel a queued job while sleeping
            r = await client.post("/_gate/jobs", json={"prompt": _sample_prompt("to cancel", seed=10)})
            jid = (await r.json())["id"]
            c_r = await client.post(f"/_gate/jobs/{jid}/cancel")
            assert c_r.status == 200
            assert (await c_r.json())["status"] == "canceled"

            # 2. Retry canceled job after waking -> executes cleanly
            await client.post("/_gate/wake", json={"profile": "tiny"})
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            retry_r = await client.post(f"/_gate/jobs/{jid}/retry")
            assert retry_r.status == 202
            for _ in range(100):
                cur = await (await client.get(f"/_gate/jobs/{jid}")).json()
                if cur["status"] == "completed":
                    break
                await asyncio.sleep(0.03)
            cur = await (await client.get(f"/_gate/jobs/{jid}")).json()
            assert cur["status"] == "completed"
            cpid = cur["client_prompt_id"]

            # 3. History fallback after ComfyUI memory wipe
            comfy.history.clear()
            hist_r = await client.get(f"/history/{cpid}")
            assert hist_r.status == 200
            hist_json = await hist_r.json()
            assert cpid in hist_json
            assert hist_json[cpid]["status"]["completed"] is True

            # 4. MCP gate_generate queues & returns archive asset URL
            mcp_r = await client.post("/_gate/mcp", json={
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tools/call",
                "params": {
                    "name": "gate_generate",
                    "arguments": {"prompt": _sample_prompt("mcp durable", seed=55), "timeout_seconds": 15},
                },
            })
            assert mcp_r.status == 200
            mcp_json = await mcp_r.json()
            assert mcp_json["result"].get("isError") is not True
            text = mcp_json["result"]["content"][0]["text"]
            assert "/_gate/archive/assets/" in text

            # 5. Non-UUID client prompt_id on POST /prompt still dispatches canonical UUID to ComfyUI
            nc_r = await client.post("/prompt", json={
                "prompt_id": "custom-short-id-42",
                "prompt": _sample_prompt("non-uuid client id", seed=77),
            })
            assert nc_r.status == 200
            nc_jid = (await nc_r.json())["job_id"]
            for _ in range(100):
                cur = await (await client.get(f"/_gate/jobs/{nc_jid}")).json()
                if cur["status"] == "completed":
                    break
                await asyncio.sleep(0.03)
            cur = await (await client.get(f"/_gate/jobs/{nc_jid}")).json()
            assert cur["status"] == "completed"
            assert str(uuid.UUID(cur["comfy_prompt_id"])) == cur["comfy_prompt_id"]
            h_custom = await (await client.get("/history/custom-short-id-42")).json()
            assert "custom-short-id-42" in h_custom

            # 6. ComfyUI frontend /api/-prefixed upload, prompt & history routes are also persisted
            form = aiohttp.FormData()
            form.add_field("image", TINY_PNG, filename="ui_ref.png", content_type="image/png")
            up_api = await client.post("/api/upload/image", data=form)
            assert up_api.status == 200
            up_api_json = await up_api.json()
            assert up_api_json["name"] == "ui_ref.png"

            pr_api = await client.post("/api/prompt", json={
                "client_id": "ui-tab-1",
                "prompt": _sample_prompt("frontend api prompt", seed=88, image_name="ui_ref.png"),
            })
            assert pr_api.status == 200
            pr_api_json = await pr_api.json()
            api_jid = pr_api_json["job_id"]
            api_cpid = pr_api_json["prompt_id"]
            for _ in range(100):
                cur = await (await client.get(f"/_gate/jobs/{api_jid}")).json()
                if cur["status"] == "completed":
                    break
                await asyncio.sleep(0.03)
            cur = await (await client.get(f"/_gate/jobs/{api_jid}")).json()
            assert cur["status"] == "completed"
            assert len(cur["inputs"]) == 1 and cur["inputs"][0]["filename"] == "ui_ref.png"
            assert len(cur["outputs"]) == 1
            assert comfy.submitted_prompts[-1]["extra_data"].get("client_id") == "ui-tab-1"

            comfy.history.clear()
            hist_api = await client.get(f"/api/history/{api_cpid}")
            assert hist_api.status == 200
            hist_api_json = await hist_api.json()
            assert api_cpid in hist_api_json
            assert hist_api_json[api_cpid]["status"]["completed"] is True
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_embed_iframe_proxies_comfyui_when_ready(tmp_path: Path) -> None:
    async with FakeComfyBackend() as comfy:
        app, client = _make_gate_client(tmp_path, comfy.base_url)
        await client.start_server()
        try:
            # While sleeping, GET / returns wake page and GET /?embed=1 returns 503
            wake_resp = await client.get("/")
            assert wake_resp.status == 200
            assert "window.__INIT__" in await wake_resp.text()

            embed_sleep = await client.get("/?embed=1")
            assert embed_sleep.status == 503

            # Wake session to READY
            wr = await client.post("/_gate/wake", json={"profile": "tiny"})
            assert wr.status == 202
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            # Top-level GET / still serves wake shell (which mounts iframe src="/?embed=1")
            shell_resp = await client.get("/")
            assert shell_resp.status == 200
            shell_html = await shell_resp.text()
            assert 'const targetUrl="/?embed=1"' in shell_html

            # Same-origin GET /?embed=1 proxies ComfyUI HTML + overlay, stripping ?embed=1 and X-Frame-Options
            embed_resp = await client.get("/?embed=1")
            assert embed_resp.status == 200
            assert "X-Frame-Options" not in embed_resp.headers
            embed_html = await embed_resp.text()
            assert "ComfyUI Mock" in embed_html
            assert "/_gate/static/overlay.js" in embed_html
            assert comfy.root_queries == [""]
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_sync_backend_history_archives_untracked_job(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gdrive_srv") as gd, FakeComfyBackend() as comfy:
        oauth_file = tmp_path / "gdrive_oauth.json"
        gd.write_oauth_file(oauth_file)
        app, client = _make_gate_client(tmp_path, comfy.base_url, oauth_file=oauth_file)
        await client.start_server()
        try:
            wr = await client.post("/_gate/wake", json={"profile": "moss-image"})
            assert wr.status == 202
            await _wait_for(lambda: app[SESSION].machine.state == State.READY)

            direct_pid = str(uuid.uuid4())
            out_name = "moss_banding_fix_00001_.png"
            comfy.outputs[out_name] = comfy.default_output_bytes
            prompt_graph = _sample_prompt("penguin holding coffee mug in Antarctica", seed=123456)
            comfy.history[direct_pid] = {
                "prompt": [0, direct_pid, prompt_graph, {"client_id": "direct-tab"}, []],
                "outputs": {
                    "26": {
                        "images": [{"filename": out_name, "subfolder": "", "type": "output"}],
                    }
                },
                "status": {"status_str": "success", "completed": True, "messages": []},
            }

            sync1 = await client.post("/_gate/jobs/sync")
            assert sync1.status == 200
            s1_json = await sync1.json()
            assert s1_json == {"ok": True, "ingested": 1}

            jobs_resp = await (await client.get("/_gate/jobs")).json()
            assert jobs_resp["total"] == 1
            job = jobs_resp["jobs"][0]
            assert job["status"] == "completed"
            assert job["comfy_prompt_id"] == direct_pid
            assert job["profile_id"] == "moss-image"
            assert "penguin holding coffee mug" in job["prompt_text"]
            assert job["seed"] == 123456
            assert len(job["outputs"]) == 1
            assert job["outputs"][0]["filename"] == out_name
            assert job["outputs"][0]["storage_backend"] == "gdrive"

            # Output file is archived & downloadable via /_gate/archive/assets/<id> even after backend clears
            aid = job["outputs"][0]["id"]
            comfy.history.clear()
            comfy.outputs.clear()
            asset_resp = await client.get(f"/_gate/archive/assets/{aid}")
            assert asset_resp.status == 200
            assert await asset_resp.read() == comfy.default_output_bytes

            # Idempotent second sync ingests 0
            sync2 = await client.post("/_gate/jobs/sync")
            assert sync2.status == 200
            assert await sync2.json() == {"ok": True, "ingested": 0}
        finally:
            await client.close()
