"""Durable job queue, ComfyUI proxy interception, input rehydration, and eager output archival."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote
from uuid import UUID, uuid4

import aiohttp
from aiohttp import web

from .config import Settings
from .db import GateDB
from .proxy import Proxy, service_headers
from .session import Session
from .state import Event, State
from .storage import ArchiveStore, save_thumbnail

log = logging.getLogger("gate.queue")

_PROMPT_TEXT_NODES = {
    "cliptextencode",
    "cliptextencodeflux",
    "cliptextencodesd3",
    "primitivestringmultiline",
    "primitivestring",
    "stringmultiline",
}
_SAMPLER_NODES = {
    "ksampler",
    "ksampleradvanced",
    "samplercustom",
    "samplercustomadvanced",
    "minimaxh3sampler",
}
_NEGATIVE_HINTS = ("worst quality", "bad anatomy", "low quality", "jpeg artifact", "watermark")
_MAX_THUMB_CAPTURE_BYTES = 20 * 1024 * 1024


def _canonical_uuid(raw: str | None = None) -> str:
    """Return a canonical lowercase hyphenated UUID string."""
    s = (raw or "").strip()
    if s:
        try:
            return str(UUID(s))
        except ValueError:
            pass
    return str(uuid4())


def _ext_from_mime(mime: str) -> str:
    guessed = mimetypes.guess_extension(mime.split(";")[0].strip()) if mime else None
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".bin"


def _guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    m, _ = mimetypes.guess_type(filename)
    return m or fallback


def _resolve_linked_text(prompt_dict: dict[str, Any], val: Any, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list) and len(val) >= 1:
        ref_id = str(val[0])
        node = prompt_dict.get(ref_id)
        if isinstance(node, dict):
            inputs = node.get("inputs") or {}
            for k in ("value", "text", "string", "prompt"):
                if k in inputs:
                    res = _resolve_linked_text(prompt_dict, inputs[k], depth + 1)
                    if res:
                        return res
    return ""


def _resolve_linked_int(prompt_dict: dict[str, Any], val: Any, depth: int = 0) -> int | None:
    if depth > 4:
        return None
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(val, list) and len(val) >= 1:
        ref_id = str(val[0])
        node = prompt_dict.get(ref_id)
        if isinstance(node, dict):
            inputs = node.get("inputs") or {}
            for k in ("value", "seed", "noise_seed"):
                if k in inputs:
                    res = _resolve_linked_int(prompt_dict, inputs[k], depth + 1)
                    if res is not None:
                        return res
    return None


def extract_prompt_text(prompt_dict: dict[str, Any]) -> str:
    """Extract a readable positive prompt summary from a ComfyUI prompt graph."""
    if not isinstance(prompt_dict, dict):
        return ""
    # 1. Follow sampler 'positive' links first when available
    for node in prompt_dict.values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "").lower()
        inputs = node.get("inputs") or {}
        if ct in _SAMPLER_NODES or "positive" in inputs:
            pos = inputs.get("positive")
            if isinstance(pos, list) and pos:
                pos_node = prompt_dict.get(str(pos[0]))
                if isinstance(pos_node, dict):
                    pos_in = pos_node.get("inputs") or {}
                    for k in ("text", "value", "prompt", "t5xxl", "clip_l"):
                        if k in pos_in:
                            txt = _resolve_linked_text(prompt_dict, pos_in[k])
                            if txt:
                                return txt[:1000]

    # 2. Fallback: scan CLIPTextEncode / PrimitiveString / text inputs, skipping obvious negatives
    candidates: list[str] = []
    for node in prompt_dict.values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "").lower()
        inputs = node.get("inputs") or {}
        if ct in _PROMPT_TEXT_NODES or "clip" in ct or "text" in inputs or "prompt" in inputs:
            for k in ("text", "prompt", "value", "t5xxl", "clip_l"):
                val = inputs.get(k)
                if isinstance(val, str) and val.strip():
                    s = val.strip()
                    low = s.lower()
                    if any(h in low for h in _NEGATIVE_HINTS):
                        continue
                    candidates.append(s)
    if candidates:
        return candidates[0][:1000]
    return ""


def extract_seed(prompt_dict: dict[str, Any]) -> int | None:
    if not isinstance(prompt_dict, dict):
        return None
    for node in prompt_dict.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        for k in ("seed", "noise_seed"):
            if k in inputs:
                val = _resolve_linked_int(prompt_dict, inputs[k])
                if val is not None:
                    return val
    return None


def iter_input_string_values(prompt_dict: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Yield (node_id, field_name, string_val) for every string scalar inside node inputs."""
    out: list[tuple[str, str, str]] = []

    def walk(node_id: str, field_name: str, obj: Any) -> None:
        if isinstance(obj, str):
            s = obj.strip()
            if s:
                out.append((node_id, field_name, s))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(node_id, str(k), v)
        elif isinstance(obj, list):
            for item in obj:
                walk(node_id, field_name, item)

    for nid, node in prompt_dict.items():
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
            walk(str(nid), "", node["inputs"])
    return out


def _replace_filename_in_prompt(prompt_dict: dict[str, Any], old_name: str, new_name: str) -> None:
    if old_name == new_name:
        return

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, str) and obj.strip() == old_name:
            return new_name
        return obj

    for nid, node in list(prompt_dict.items()):
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
            node["inputs"] = walk(node["inputs"])


async def startup_restore(settings: Settings, store: ArchiveStore, db_path: Path) -> GateDB:
    """Restore gate.db from remote archive if missing locally, then mark interrupted jobs."""
    await store.restore_db_if_missing(db_path)
    db = GateDB(db_path)
    db.mark_all_running_interrupted("gate restarted")
    return db


class JobManager:
    """Durable serial job queue, input rehydration, eager output archival, and DB backup coordinator."""

    def __init__(
        self,
        settings: Settings,
        session: Session,
        proxy: Proxy,
        db: GateDB,
        store: ArchiveStore,
        thumbs_dir: Path | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        self.settings = settings
        self.session = session
        self.proxy = proxy
        self.db = db
        self.store = store
        self.thumbs_dir = thumbs_dir or Path(settings.thumbs_dir or (Path(settings.state_dir).parent / "thumbs"))
        self.poll_interval_s = poll_interval_s

        self._vm_uploaded_files: set[str] = set()
        self._filename_aliases: dict[str, str] = {}
        self._active_job_id: str | None = None
        self._canceled_jobs: set[str] = set()
        self._wake_event = asyncio.Event()
        self._terminal_waiters: dict[str, list[asyncio.Event]] = {}
        self._backup_lock = asyncio.Lock()
        self._backup_task: asyncio.Task | None = None
        self._db_dirty: bool = False
        self._stopped: bool = False
        self._last_state: State = session.machine.state
        session.machine.listeners.append(self._on_session_event)

    @property
    def db_dirty(self) -> bool:
        return self._db_dirty

    def _on_session_event(self, ev: Event) -> None:
        prev = self._last_state
        self._last_state = ev.state
        if prev == State.READY and ev.state != State.READY:
            self._vm_uploaded_files.clear()
            self._filename_aliases.clear()
            cnt = self.db.mark_all_running_interrupted(
                f"VM transitioned from ready to {ev.state.value}: {ev.note}"
            )
            if cnt > 0:
                self.mark_db_dirty()
                self.session.bus.publish("job", {"event": "interrupted", "count": cnt})
        elif ev.state == State.READY:
            self._vm_uploaded_files.clear()
            self._filename_aliases.clear()
            self._wake_event.set()

    def _backend_headers(self) -> dict[str, str]:
        h = service_headers(self.proxy.token_file)
        backend_host = self.proxy.backend.split("://", 1)[-1]
        h["Host"] = backend_host
        h["Origin"] = self.proxy.backend
        return h

    def _notify_terminal(self, job_id: str) -> None:
        waiters = self._terminal_waiters.pop(job_id, [])
        for ev in waiters:
            ev.set()

    def _publish_job_event(self, job: dict[str, Any] | None, event: str = "update") -> None:
        if not job:
            return
        payload = {
            "event": event,
            "id": job["id"],
            "status": job["status"],
            "profile_id": job["profile_id"],
            "queue": self.active_queue_summary(),
        }
        self.session.bus.publish("job", payload)
        if job["status"] in ("completed", "failed", "canceled"):
            self._notify_terminal(job["id"])

    def queue_summary(self, profile_id: str | None = None) -> dict[str, int]:
        return self.db.count_jobs_by_status(profile_id)

    def active_queue_summary(self) -> dict[str, int]:
        pid = self.session.machine.profile if self.session.machine.state == State.READY else None
        return self.queue_summary(pid)

    def has_active_work_for(self, profile_id: str | None) -> bool:
        if self._active_job_id is not None:
            return True
        if not profile_id:
            return False
        return self.db.next_dispatchable_job(
            profile_id, include_interrupted_retry=self.settings.queue_auto_retry
        ) is not None

    # ── DB hot backup ─────────────────────────────────────────────────────────
    def mark_db_dirty(self) -> None:
        self._db_dirty = True
        if self._stopped:
            return
        if self._backup_task is None or self._backup_task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._backup_task = loop.create_task(self._debounced_backup())

    async def _debounced_backup(self) -> None:
        try:
            delay = self.settings.db_backup_debounce_s
            if delay > 0:
                await asyncio.sleep(delay)
            await self.backup_now()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("debounced DB backup failed: %s", e)

    async def backup_now(self) -> dict[str, Any]:
        async with self._backup_lock:
            snap_path = Path(self.settings.state_dir) / f"gate.db.snap.{uuid4().hex[:8]}.tmp"
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.db.create_backup_snapshot, snap_path)
                meta = {
                    "backed_up_at": self.session.clock(),
                    "counts": self.db.count_jobs_by_status(),
                }
                ok = await self.store.backup_db(snap_path, meta)
                if ok:
                    self._db_dirty = False
                return {"ok": ok, "backend": self.store.backend_name, "meta": meta}
            finally:
                snap_path.unlink(missing_ok=True)

    # ── Input storage & upload handling ───────────────────────────────────────
    async def store_input_bytes(
        self,
        filename: str,
        data: bytes,
        mime: str = "",
        subfolder: str = "",
    ) -> dict[str, Any]:
        clean_name = Path(filename or "upload.bin").name or "upload.bin"
        resolved_mime = mime or _guess_mime(clean_name)
        sha256 = hashlib.sha256(data).hexdigest()
        ext = Path(clean_name).suffix.lower() or _ext_from_mime(resolved_mime)
        blob = await self.store.put_input(sha256, ext, data, resolved_mime)
        thumb_rel = save_thumbnail(self.thumbs_dir, "_inputs", sha256[:16], 0, data, resolved_mime)
        asset = self.db.upsert_input_asset(
            sha256=sha256,
            filename=clean_name,
            mime=resolved_mime,
            size_bytes=len(data),
            storage_backend=blob.storage_backend,
            storage_key=blob.storage_key,
            remote_id=blob.remote_id,
            thumb_relpath=thumb_rel,
        )
        key = f"{subfolder}/{clean_name}" if subfolder else clean_name
        self._filename_aliases[clean_name] = asset["id"]
        self._filename_aliases[key] = asset["id"]
        self.mark_db_dirty()
        return asset

    @staticmethod
    async def _parse_upload_multipart(request: web.Request) -> dict[str, Any]:
        reader = await request.multipart()
        file_bytes: bytes | None = None
        filename = ""
        mime = ""
        field_name = "image"
        subfolder = ""
        upload_type = "input"
        overwrite = "false"

        while True:
            part = await reader.next()
            if part is None:
                break
            name = part.name or ""
            if part.filename is not None or name in ("image", "file"):
                raw = await part.read(decode=False)
                if file_bytes is None or name in ("image", "file"):
                    file_bytes = raw
                    filename = Path(part.filename or "upload.bin").name or "upload.bin"
                    mime = part.headers.get("Content-Type") or _guess_mime(filename)
                    field_name = name or "image"
            elif name == "subfolder":
                subfolder = (await part.text()).strip().strip("/")
            elif name == "type":
                upload_type = (await part.text()).strip() or "input"
            elif name == "overwrite":
                overwrite = (await part.text()).strip() or "false"

        if file_bytes is None:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "bad_request", "message": "missing multipart file field ('image' or 'file')"}),
                content_type="application/json",
            )
        return {
            "data": file_bytes,
            "filename": filename,
            "mime": mime,
            "field_name": field_name,
            "subfolder": subfolder,
            "type": upload_type,
            "overwrite": overwrite,
        }

    async def handle_offline_upload(self, request: web.Request) -> web.Response:
        parsed = await self._parse_upload_multipart(request)
        asset = await self.store_input_bytes(
            parsed["filename"],
            parsed["data"],
            mime=parsed["mime"],
            subfolder=parsed["subfolder"],
        )
        return web.json_response({
            **asset,
            "name": asset["filename"],
            "subfolder": parsed["subfolder"],
            "type": parsed["type"],
        }, status=201)

    async def handle_upload_proxy(self, request: web.Request) -> web.Response:
        self.session.touch()
        parsed = await self._parse_upload_multipart(request)
        asset = await self.store_input_bytes(
            parsed["filename"],
            parsed["data"],
            mime=parsed["mime"],
            subfolder=parsed["subfolder"],
        )
        form = aiohttp.FormData(quote_fields=False)
        form.add_field(
            parsed["field_name"] if parsed["field_name"] in ("image", "file") else "image",
            parsed["data"],
            filename=parsed["filename"],
            content_type=parsed["mime"],
        )
        if parsed["subfolder"]:
            form.add_field("subfolder", parsed["subfolder"])
        if parsed["type"]:
            form.add_field("type", parsed["type"])
        if parsed["overwrite"]:
            form.add_field("overwrite", parsed["overwrite"])

        client = await self.proxy.client()
        url = f"{self.proxy.backend}{request.path}"
        async with client.post(
            url,
            data=form,
            headers=self._backend_headers(),
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            raw = await resp.read()
            if resp.status >= 400:
                return web.Response(
                    status=resp.status,
                    body=raw,
                    content_type=resp.headers.get("Content-Type", "application/json"),
                )
            try:
                out = json.loads(raw)
            except ValueError:
                out = {"name": parsed["filename"], "subfolder": parsed["subfolder"], "type": parsed["type"]}
            if isinstance(out, dict):
                b_name = str(out.get("name") or parsed["filename"])
                b_sub = str(out.get("subfolder") or parsed["subfolder"] or "").strip("/")
                vm_key = f"{b_sub}/{b_name}" if b_sub else b_name
                self._vm_uploaded_files.add(b_name)
                self._vm_uploaded_files.add(vm_key)
                self._filename_aliases[b_name] = asset["id"]
                self._filename_aliases[vm_key] = asset["id"]
                if b_name != parsed["filename"]:
                    aliased = self.db.upsert_input_asset(
                        sha256=asset["sha256"],
                        filename=b_name,
                        mime=asset["mime"],
                        size_bytes=asset["size_bytes"],
                        storage_backend=asset["storage_backend"],
                        storage_key=asset["storage_key"],
                        remote_id=asset["remote_id"],
                        thumb_relpath=asset["thumb_relpath"],
                    )
                    self._filename_aliases[b_name] = aliased["id"]
                    self._filename_aliases[vm_key] = aliased["id"]
                return web.json_response(out, status=resp.status)
            return web.Response(status=resp.status, body=raw, content_type="application/json")

    # ── Enqueue & Proxy Interception ──────────────────────────────────────────
    def _match_input_asset(self, filename: str) -> dict[str, Any] | None:
        aid = self._filename_aliases.get(filename)
        if aid:
            a = self.db.get_asset(aid)
            if a:
                return a
        return self.db.find_input_asset_by_filename_or_sha(filename=filename)

    async def enqueue_prompt(
        self,
        prompt_dict: dict[str, Any],
        profile_id: str,
        *,
        submitted_by: str = "",
        workflow_id: str = "",
        extra_data: dict[str, Any] | None = None,
        extra_input_filenames: list[str] | None = None,
        input_asset_ids: list[str] | None = None,
        client_prompt_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(prompt_dict, dict) or not prompt_dict:
            raise ValueError("prompt must be a non-empty JSON object")

        raw_cpid = (client_prompt_id or "").strip()
        if raw_cpid:
            try:
                cpid = str(UUID(raw_cpid))
            except ValueError:
                cpid = raw_cpid
        else:
            cpid = str(uuid4())
        prompt_text = extract_prompt_text(prompt_dict)
        seed = extract_seed(prompt_dict)
        job = self.db.create_job(
            client_prompt_id=cpid,
            profile_id=profile_id,
            prompt_json=prompt_dict,
            workflow_id=workflow_id,
            status="queued",
            submitted_by=submitted_by,
            extra_data_json=extra_data or {},
            prompt_text=prompt_text,
            seed=seed,
            max_retries=self.settings.queue_max_retries,
            created_at=self.session.clock(),
        )

        linked_ids: set[str] = set()
        link_idx = 0
        for nid, field_name, val in iter_input_string_values(prompt_dict):
            asset = self._match_input_asset(val)
            if asset and asset["id"] not in linked_ids:
                self.db.link_job_asset(job["id"], asset["id"], "input", node_id=nid, field_name=field_name, idx=link_idx)
                linked_ids.add(asset["id"])
                link_idx += 1

        for fname in extra_input_filenames or []:
            asset = self._match_input_asset(str(fname).strip())
            if asset and asset["id"] not in linked_ids:
                self.db.link_job_asset(job["id"], asset["id"], "input", idx=link_idx)
                linked_ids.add(asset["id"])
                link_idx += 1

        for aid in input_asset_ids or []:
            asset = self.db.get_asset(str(aid).strip())
            if asset and asset["id"] not in linked_ids:
                self.db.link_job_asset(job["id"], asset["id"], "input", idx=link_idx)
                linked_ids.add(asset["id"])
                link_idx += 1

        full_job = self.db.get_job(job["id"], include_prompt=True) or job
        self._wake_event.set()
        self.mark_db_dirty()
        self._publish_job_event(full_job, "queued")
        return full_job

    async def handle_prompt_proxy(self, request: web.Request) -> web.Response:
        self.session.touch()
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request", "message": "invalid JSON"}, status=400)
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), dict) or not body["prompt"]:
            return web.json_response(
                {"error": {"type": "invalid_prompt", "message": "prompt must be a non-empty object"}, "node_errors": {}},
                status=400,
            )
        extra_data = dict(body.get("extra_data") or {}) if isinstance(body.get("extra_data"), dict) else {}
        if body.get("client_id") and "client_id" not in extra_data:
            extra_data["client_id"] = body["client_id"]

        profile_id = self.session.machine.profile or self.settings.default_profile
        ident = request.get("identity")
        submitted_by = getattr(ident, "email", "") if ident else ""
        job = await self.enqueue_prompt(
            body["prompt"],
            profile_id=profile_id,
            submitted_by=submitted_by,
            extra_data=extra_data,
            client_prompt_id=body.get("prompt_id") if isinstance(body.get("prompt_id"), str) else None,
        )
        return web.json_response({
            "prompt_id": job["client_prompt_id"],
            "number": 1,
            "node_errors": {},
            "job_id": job["id"],
        })

    async def handle_history_proxy(self, request: web.Request) -> web.StreamResponse:
        self.session.touch()
        path = request.path
        norm = path[4:] if path.startswith("/api/") else path
        if norm in ("/history", "/history/"):
            return await self.proxy.handle(request)
        pid = norm[len("/history/"):].strip("/")
        try:
            client = await self.proxy.client()
            async with client.get(
                f"{self.proxy.backend}{request.raw_path}",
                headers=self._backend_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, dict) and data:
                        return web.json_response(data)
        except Exception:  # noqa: BLE001
            pass

        job = self.db.get_job(pid, include_prompt=True)
        if not job:
            return web.json_response({})
        outputs: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for out in job.get("outputs") or []:
            nid = out.get("node_id") or "0"
            field = out.get("field_name") or "images"
            outputs.setdefault(nid, {}).setdefault(field, []).append({
                "filename": out["filename"],
                "subfolder": "",
                "type": "output",
                "asset_id": out["id"],
                "url": out["url"],
            })
        status_str = "success" if job["status"] == "completed" else ("error" if job["status"] == "failed" else job["status"])
        return web.json_response({
            pid: {
                "prompt": [0, pid, job.get("prompt") or {}, job.get("extra_data") or {}, []],
                "outputs": outputs,
                "status": {
                    "status_str": status_str,
                    "completed": job["status"] == "completed",
                    "messages": [],
                },
            }
        })

    async def wait_for_job(self, job_id: str, timeout_s: float = 180.0) -> dict[str, Any] | None:
        job = self.db.get_job(job_id, include_prompt=True)
        if not job:
            return None
        if job["status"] in ("completed", "failed", "canceled"):
            return job
        ev = asyncio.Event()
        self._terminal_waiters.setdefault(job_id, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pass
        finally:
            lst = self._terminal_waiters.get(job_id)
            if lst and ev in lst:
                lst.remove(ev)
        return self.db.get_job(job_id, include_prompt=True)

    # ── Job Controls ──────────────────────────────────────────────────────────
    async def cancel_job(self, job_id: str) -> tuple[dict[str, Any] | None, str]:
        job = self.db.get_job(job_id, include_prompt=False)
        if not job:
            return None, "not_found"
        if job["status"] in ("completed", "failed", "canceled"):
            return job, "already_finished"
        self._canceled_jobs.add(job_id)
        if job["status"] in ("running", "archiving") and self.session.machine.state == State.READY:
            try:
                client = await self.proxy.client()
                async with client.post(
                    f"{self.proxy.backend}/interrupt",
                    headers=self._backend_headers(),
                    json={},
                    timeout=aiohttp.ClientTimeout(total=5),
                ):
                    pass
            except Exception:  # noqa: BLE001
                pass
        updated = self.db.update_job_status(
            job_id,
            "canceled",
            finished_at=self.session.clock(),
            error_message="canceled by user",
        )
        self.mark_db_dirty()
        self._publish_job_event(updated, "canceled")
        return updated, ""

    async def retry_job(self, job_id: str) -> tuple[dict[str, Any] | None, str]:
        job = self.db.get_job(job_id, include_prompt=True)
        if not job:
            return None, "not_found"
        if job["status"] in ("queued", "running", "archiving"):
            return job, "already_active"
        if job["status"] in ("failed", "interrupted", "canceled"):
            updated = self.db.update_job_status(
                job_id,
                "queued",
                retries=0,
                error_message="",
                error_node="",
                started_at=None,
                finished_at=None,
                elapsed_s=None,
                est_cu=None,
            )
            self._canceled_jobs.discard(job_id)
            self._wake_event.set()
            self.mark_db_dirty()
            self._publish_job_event(updated, "queued")
            return updated, ""
        # Completed job -> clone into a fresh queued job
        new_job = await self.enqueue_prompt(
            job.get("prompt") or {},
            profile_id=job["profile_id"],
            submitted_by=job.get("submitted_by", ""),
            workflow_id=job.get("workflow_id", ""),
            extra_data=job.get("extra_data") or {},
            input_asset_ids=[inp["id"] for inp in job.get("inputs") or []],
        )
        return new_job, ""

    async def delete_job(self, job_id: str, delete_files: bool = False) -> bool:
        res = self.db.delete_job(job_id, delete_unshared_outputs=delete_files)
        if not res:
            return False
        if delete_files:
            for asset in res.get("unshared_assets") or []:
                try:
                    await self.store.delete_blob(asset["storage_key"], asset.get("remote_id", ""))
                except Exception as e:  # noqa: BLE001
                    log.warning("failed to delete blob %s: %s", asset.get("storage_key"), e)
                thumb_rel = asset.get("thumb_relpath") or ""
                if thumb_rel:
                    try:
                        (self.thumbs_dir / thumb_rel).unlink(missing_ok=True)
                    except Exception:
                        pass
        self.mark_db_dirty()
        self.session.bus.publish("job", {"event": "deleted", "id": job_id, "queue": self.active_queue_summary()})
        return True

    # ── Serial 1-in-flight Dispatcher Loop ────────────────────────────────────
    def stop(self) -> None:
        self._stopped = True
        self._wake_event.set()
        if self._backup_task and not self._backup_task.done():
            self._backup_task.cancel()

    async def run(self) -> None:
        try:
            while not self._stopped:
                if self.session.machine.state == State.READY and self._active_job_id is None:
                    pid = self.session.machine.profile
                    if pid:
                        job = self.db.next_dispatchable_job(
                            pid, include_interrupted_retry=self.settings.queue_auto_retry
                        )
                        if job:
                            await self._execute_job(job)
                            continue
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def _rehydrate_inputs(
        self, job: dict[str, Any], prompt_to_send: dict[str, Any]
    ) -> tuple[bool, bool, str]:
        """Ensure all input assets for `job` exist on the current ComfyUI VM.
        Returns (ok, retryable_error, error_message).
        """
        client = await self.proxy.client()
        for inp in job.get("inputs") or []:
            fname = inp["filename"]
            if fname in self._vm_uploaded_files:
                continue
            ext = Path(fname).suffix.lower() or _ext_from_mime(inp["mime"])
            data = await self.store.get_input(inp["sha256"], ext, inp.get("remote_id", ""))
            if data is None:
                return False, False, f"input asset '{fname}' missing from archive store"

            form = aiohttp.FormData(quote_fields=False)
            form.add_field("image", data, filename=fname, content_type=inp["mime"])
            form.add_field("overwrite", "true")
            try:
                async with client.post(
                    f"{self.proxy.backend}/upload/image",
                    data=form,
                    headers=self._backend_headers(),
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status >= 500:
                        return False, True, f"input rehydrate failed ({resp.status})"
                    if resp.status >= 400:
                        txt = await resp.text()
                        return False, False, f"input upload rejected ({resp.status}): {txt[:300]}"
                    try:
                        out = await resp.json()
                    except ValueError:
                        out = {}
                    uploaded_name = str((out or {}).get("name") or fname)
                    self._vm_uploaded_files.add(fname)
                    self._vm_uploaded_files.add(uploaded_name)
                    if uploaded_name != fname:
                        _replace_filename_in_prompt(prompt_to_send, fname, uploaded_name)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                return False, True, f"input rehydrate connection error: {exc}"
        return True, False, ""

    async def _archive_outputs(
        self, job: dict[str, Any], history_item: dict[str, Any], start_time: float
    ) -> None:
        job_id = job["id"]
        profile_id = job["profile_id"]
        yyyy_mm = datetime.fromtimestamp(start_time, tz=timezone.utc).strftime("%Y-%m")
        outputs = history_item.get("outputs") or {}

        collected: list[tuple[str, str, str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for nid, node_out in outputs.items():
            if not isinstance(node_out, dict):
                continue
            for key in ("images", "videos", "gifs", "audio"):
                for entry in node_out.get(key) or []:
                    if not isinstance(entry, dict):
                        continue
                    fn = str(entry.get("filename") or "").strip()
                    sub = str(entry.get("subfolder") or "").strip()
                    ftype = str(entry.get("type") or "output").strip()
                    if not fn or (fn, sub, ftype) in seen:
                        continue
                    seen.add((fn, sub, ftype))
                    collected.append((str(nid), key, fn, sub, ftype))

        client = await self.proxy.client()
        for idx, (nid, field_key, fn, sub, ftype) in enumerate(collected):
            view_url = (
                f"{self.proxy.backend}/view?filename={quote(fn)}"
                f"&subfolder={quote(sub)}&type={quote(ftype)}"
            )
            async with client.get(
                view_url,
                headers=self._backend_headers(),
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"failed to fetch output '{fn}' from ComfyUI ({resp.status})")
                mime = (resp.headers.get("Content-Type") or _guess_mime(fn)).split(";")[0].strip()
                cl = resp.headers.get("Content-Length")
                total_size = int(cl) if (cl and cl.isdigit()) else None

                thumb_buf = bytearray()
                thumb_overflow = False

                async def _chunks() -> AsyncIterator[bytes]:
                    nonlocal thumb_overflow
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        if not thumb_overflow:
                            if len(thumb_buf) + len(chunk) <= _MAX_THUMB_CAPTURE_BYTES:
                                thumb_buf.extend(chunk)
                            else:
                                thumb_overflow = True
                                thumb_buf.clear()
                        yield chunk

                blob = await self.store.put_output_stream(
                    profile_id, yyyy_mm, job_id, fn, _chunks(), total_size, mime
                )
                thumb_rel = ""
                if not thumb_overflow and thumb_buf:
                    thumb_rel = save_thumbnail(
                        self.thumbs_dir, profile_id, job_id, idx, bytes(thumb_buf), mime
                    )
                asset = self.db.add_output_asset(
                    sha256=blob.sha256,
                    filename=fn,
                    mime=blob.mime or mime,
                    size_bytes=blob.size_bytes,
                    storage_backend=blob.storage_backend,
                    storage_key=blob.storage_key,
                    remote_id=blob.remote_id,
                    thumb_relpath=thumb_rel,
                )
                self.db.link_job_asset(job_id, asset["id"], "output", node_id=nid, field_name=field_key, idx=idx)

        refreshed = self.db.get_job(job_id, include_prompt=True) or job
        manifest = {
            "id": refreshed["id"],
            "client_prompt_id": refreshed["client_prompt_id"],
            "comfy_prompt_id": refreshed["comfy_prompt_id"],
            "profile_id": profile_id,
            "workflow_id": refreshed.get("workflow_id", ""),
            "submitted_by": refreshed.get("submitted_by", ""),
            "accelerator": refreshed.get("accelerator", ""),
            "prompt_text": refreshed.get("prompt_text", ""),
            "seed": refreshed.get("seed"),
            "created_at": refreshed.get("created_at"),
            "started_at": refreshed.get("started_at"),
            "outputs": [
                {
                    "id": o["id"],
                    "filename": o["filename"],
                    "mime": o["mime"],
                    "size_bytes": o["size_bytes"],
                    "sha256": o["sha256"],
                    "storage_key": o["storage_key"],
                    "remote_id": o["remote_id"],
                }
                for o in refreshed.get("outputs") or []
            ],
        }
        await self.store.put_job_manifest(profile_id, yyyy_mm, job_id, manifest)

    async def _execute_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        if job_id in self._canceled_jobs:
            updated = self.db.update_job_status(job_id, "canceled", finished_at=self.session.clock())
            self._publish_job_event(updated, "canceled")
            return

        self._active_job_id = job_id
        start_time = self.session.clock()
        was_interrupted = job["status"] == "interrupted"
        new_retries = int(job.get("retries") or 0) + (1 if was_interrupted else 0)
        accel = self.session.active_accelerator
        try:
            updated = self.db.update_job_status(
                job_id,
                "running",
                retries=new_retries,
                started_at=start_time,
                accelerator=accel,
                error_message="",
                error_node="",
            )
            self._publish_job_event(updated, "running")

            prompt_to_send = copy.deepcopy(job.get("prompt") or {})
            ok, retryable, err_msg = await self._rehydrate_inputs(job, prompt_to_send)
            if not ok:
                new_status = "interrupted" if retryable else "failed"
                updated = self.db.update_job_status(
                    job_id,
                    new_status,
                    error_message=err_msg,
                    finished_at=None if retryable else self.session.clock(),
                )
                self.mark_db_dirty()
                self._publish_job_event(updated, new_status)
                if retryable:
                    await asyncio.sleep(min(2.0, self.poll_interval_s))
                return

            if job_id in self._canceled_jobs:
                updated = self.db.update_job_status(job_id, "canceled", finished_at=self.session.clock())
                self._publish_job_event(updated, "canceled")
                return

            dispatch_pid = _canonical_uuid(job.get("client_prompt_id"))
            payload: dict[str, Any] = {
                "prompt": prompt_to_send,
                "prompt_id": dispatch_pid,
            }
            ed = job.get("extra_data") or {}
            if ed:
                payload["extra_data"] = ed
                if ed.get("client_id"):
                    payload["client_id"] = ed["client_id"]

            client = await self.proxy.client()
            try:
                async with client.post(
                    f"{self.proxy.backend}/prompt",
                    json=payload,
                    headers=self._backend_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if 400 <= resp.status < 500:
                        err_text = await resp.text()
                        updated = self.db.update_job_status(
                            job_id,
                            "failed",
                            error_message=f"ComfyUI rejected prompt ({resp.status}): {err_text[:500]}",
                            finished_at=self.session.clock(),
                        )
                        self.mark_db_dirty()
                        self._publish_job_event(updated, "failed")
                        return
                    if resp.status >= 500:
                        err_text = await resp.text()
                        updated = self.db.update_job_status(
                            job_id,
                            "interrupted",
                            error_message=f"ComfyUI server error ({resp.status}): {err_text[:300]}",
                        )
                        self.mark_db_dirty()
                        self._publish_job_event(updated, "interrupted")
                        await asyncio.sleep(min(2.0, self.poll_interval_s))
                        return
                    p_out = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                updated = self.db.update_job_status(
                    job_id,
                    "interrupted",
                    error_message=f"prompt dispatch error: {exc}",
                )
                self.mark_db_dirty()
                self._publish_job_event(updated, "interrupted")
                await asyncio.sleep(min(2.0, self.poll_interval_s))
                return

            comfy_pid = str((p_out or {}).get("prompt_id") or dispatch_pid)
            self.db.update_job_status(job_id, "running", comfy_prompt_id=comfy_pid)

            # Step C: Poll /history/{comfy_pid}
            missing_from_queue_streak = 0
            poll_count = 0
            while not self._stopped:
                await asyncio.sleep(self.poll_interval_s)
                if self.session.machine.state != State.READY:
                    # Listener already marked running jobs interrupted
                    return
                if job_id in self._canceled_jobs:
                    try:
                        async with client.post(
                            f"{self.proxy.backend}/interrupt",
                            headers=self._backend_headers(),
                            json={},
                            timeout=aiohttp.ClientTimeout(total=5),
                        ):
                            pass
                    except Exception:  # noqa: BLE001
                        pass
                    updated = self.db.update_job_status(
                        job_id,
                        "canceled",
                        finished_at=self.session.clock(),
                        error_message="canceled by user",
                    )
                    self.mark_db_dirty()
                    self._publish_job_event(updated, "canceled")
                    return

                poll_count += 1
                self.session.touch()
                try:
                    async with client.get(
                        f"{self.proxy.backend}/history/{quote(comfy_pid)}",
                        headers=self._backend_headers(),
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as hr:
                        if hr.status == 200:
                            h_out = await hr.json()
                            if isinstance(h_out, dict) and comfy_pid in h_out:
                                item = h_out[comfy_pid]
                                st = item.get("status") or {}
                                status_str = str(st.get("status_str") or "").lower()
                                if status_str == "error":
                                    err_node = ""
                                    err_detail = ""
                                    for msg in st.get("messages") or []:
                                        if isinstance(msg, (list, tuple)) and len(msg) >= 2 and msg[0] == "execution_error":
                                            data = msg[1] if isinstance(msg[1], dict) else {}
                                            err_node = str(data.get("node_id") or "")
                                            err_detail = str(data.get("exception_message") or data.get("message") or "")
                                            break
                                    if not err_detail:
                                        err_detail = json.dumps(st)[:500]
                                    fin = self.session.clock()
                                    el = round(max(0.0, fin - start_time), 3)
                                    cu = round((el / 3600.0) * self.session._cu_per_hour(), 6)
                                    updated = self.db.update_job_status(
                                        job_id,
                                        "failed",
                                        error_node=err_node,
                                        error_message=err_detail[:1000],
                                        finished_at=fin,
                                        elapsed_s=el,
                                        est_cu=cu,
                                    )
                                    self.mark_db_dirty()
                                    self._publish_job_event(updated, "failed")
                                    return

                                if st.get("completed") or status_str == "success" or item.get("outputs"):
                                    updated = self.db.update_job_status(job_id, "archiving")
                                    self._publish_job_event(updated, "archiving")
                                    try:
                                        await self._archive_outputs(job, item, start_time)
                                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                                        updated = self.db.update_job_status(
                                            job_id,
                                            "interrupted",
                                            error_message=f"archive interrupted: {exc}",
                                        )
                                        self.mark_db_dirty()
                                        self._publish_job_event(updated, "interrupted")
                                        return
                                    except Exception as exc:  # noqa: BLE001
                                        fin = self.session.clock()
                                        el = round(max(0.0, fin - start_time), 3)
                                        cu = round((el / 3600.0) * self.session._cu_per_hour(), 6)
                                        updated = self.db.update_job_status(
                                            job_id,
                                            "failed",
                                            error_message=f"archive error: {exc}",
                                            finished_at=fin,
                                            elapsed_s=el,
                                            est_cu=cu,
                                        )
                                        self.mark_db_dirty()
                                        self._publish_job_event(updated, "failed")
                                        return

                                    fin = self.session.clock()
                                    el = round(max(0.0, fin - start_time), 3)
                                    cu = round((el / 3600.0) * self.session._cu_per_hour(), 6)
                                    updated = self.db.update_job_status(
                                        job_id,
                                        "completed",
                                        finished_at=fin,
                                        elapsed_s=el,
                                        est_cu=cu,
                                    )
                                    self.session.touch()
                                    self.mark_db_dirty()
                                    self._publish_job_event(updated, "completed")
                                    return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if self.session.machine.state != State.READY:
                        return
                    # Transient tunnel hiccup or crashed VM; check queue/continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("history poll error for %s: %s", job_id, exc)

                # Periodically check /queue to detect silent VM crash/restart
                if poll_count % 5 == 0:
                    try:
                        async with client.get(
                            f"{self.proxy.backend}/queue",
                            headers=self._backend_headers(),
                            timeout=aiohttp.ClientTimeout(total=8),
                        ) as qr:
                            if qr.status == 200:
                                qd = await qr.json()
                                running_ids = {str(x[1]) for x in (qd.get("queue_running") or []) if isinstance(x, list) and len(x) > 1}
                                pending_ids = {str(x[1]) for x in (qd.get("queue_pending") or []) if isinstance(x, list) and len(x) > 1}
                                if comfy_pid not in running_ids and comfy_pid not in pending_ids:
                                    missing_from_queue_streak += 1
                                    if missing_from_queue_streak >= 2:
                                        # Re-check history once before declaring crash
                                        async with client.get(
                                            f"{self.proxy.backend}/history/{quote(comfy_pid)}",
                                            headers=self._backend_headers(),
                                            timeout=aiohttp.ClientTimeout(total=8),
                                        ) as hr2:
                                            h2 = await hr2.json() if hr2.status == 200 else {}
                                            if not isinstance(h2, dict) or comfy_pid not in h2:
                                                self._vm_uploaded_files.clear()
                                                updated = self.db.update_job_status(
                                                    job_id,
                                                    "interrupted",
                                                    error_message="prompt vanished from ComfyUI queue/history (possible VM restart)",
                                                )
                                                self.mark_db_dirty()
                                                self._publish_job_event(updated, "interrupted")
                                                return
                                else:
                                    missing_from_queue_streak = 0
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        pass
        finally:
            self._active_job_id = None
