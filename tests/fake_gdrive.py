"""Reusable local Google Drive API v3 + OAuth2 fake server backed by disk."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from aiohttp import web

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from apps.gate.storage import parse_range_header


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _parse_multipart_related(content_type: str, body: bytes) -> tuple[dict[str, Any], bytes, str]:
    m = re.search(r'boundary=([^\s;]+)', content_type)
    if not m:
        raise ValueError("Missing multipart boundary")
    boundary = m.group(1).strip('"').encode("utf-8")
    delim = b"--" + boundary
    sections = body.split(delim)
    parts: list[tuple[dict[str, str], bytes]] = []
    for sec in sections:
        if not sec or sec.startswith(b"--"):
            continue
        sec = sec.lstrip(b"\r\n")
        if sec.endswith(b"\r\n"):
            sec = sec[:-2]
        sep = sec.find(b"\r\n\r\n")
        if sep < 0:
            continue
        raw_hdr = sec[:sep].decode("utf-8", errors="replace")
        payload = sec[sep + 4 :]
        hdrs: dict[str, str] = {}
        for line in raw_hdr.split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
        parts.append((hdrs, payload))
    if len(parts) < 2:
        raise ValueError("multipart/related requires metadata and media parts")
    meta = json.loads(parts[0][1].decode("utf-8"))
    media_mime = parts[1][0].get("content-type", meta.get("mimeType", "application/octet-stream"))
    return meta, parts[1][1], media_mime


def _match_drive_query(item: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    # Split on case-insensitive ' and ' outside quotes
    clauses = re.split(r"\s+and\s+", q.strip(), flags=re.IGNORECASE)
    for clause in clauses:
        c = clause.strip()
        if not c:
            continue
        m_parent = re.match(r"^'([^']*)'\s+in\s+parents$", c, flags=re.IGNORECASE)
        if m_parent:
            parent_id = m_parent.group(1)
            parents = item.get("parents") or ["root"]
            if parent_id not in parents:
                return False
            continue
        m_eq = re.match(r"^([A-Za-z0-9_]+)\s*=\s*(.+)$", c)
        if m_eq:
            field, raw_val = m_eq.group(1), m_eq.group(2).strip()
            if (raw_val.startswith("'") and raw_val.endswith("'")) or (
                raw_val.startswith('"') and raw_val.endswith('"')
            ):
                val: Any = raw_val[1:-1].replace("\\'", "'").replace("\\\\", "\\")
            elif raw_val.lower() == "false":
                val = False
            elif raw_val.lower() == "true":
                val = True
            else:
                val = raw_val
            if field == "trashed":
                if bool(item.get("trashed", False)) != bool(val):
                    return False
            elif item.get(field) != val:
                return False
            continue
    return True


class FakeGDriveServer:
    """Disk-backed fake Google Drive API v3 + OAuth2 server for tests & local E2E."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        client_id: str = "test-client-id",
        client_secret: str = "test-client-secret",
        refresh_token: str = "test-refresh-token",
        token_ttl_s: float = 3600.0,
    ):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir = self.root_dir / "blobs"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.root_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.root_dir / "metadata.json"

        self.host = host
        self._requested_port = int(port)
        self.port = int(port)

        self.valid_client_id = client_id
        self.valid_client_secret = client_secret
        self.valid_refresh_token = refresh_token
        self.token_ttl_s = float(token_ttl_s)

        self._tokens: dict[str, float] = {}
        self._files: dict[str, dict[str, Any]] = {}
        self._upload_sessions: dict[str, dict[str, Any]] = {}
        self._transient_errors: list[int] = []

        # Observability counters for tests
        self.token_refresh_count: int = 0
        self.upload_count: int = 0
        self.resumable_put_count: int = 0
        self.download_count: int = 0
        self.delete_count: int = 0

        self._load_state()
        self._app = self._build_app()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def token_uri(self) -> str:
        return f"{self.base_url}/token"

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/drive/v3"

    @property
    def upload_base(self) -> str:
        return f"{self.base_url}/upload/drive/v3"

    def write_oauth_file(
        self,
        dest: str | Path,
        *,
        root_folder_id: str = "",
    ) -> Path:
        p = Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, str] = {
            "client_id": self.valid_client_id,
            "client_secret": self.valid_client_secret,
            "refresh_token": self.valid_refresh_token,
            "token_uri": self.token_uri,
            "api_base": self.api_base,
            "upload_base": self.upload_base,
        }
        if root_folder_id:
            payload["root_folder_id"] = root_folder_id
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return p

    def expire_current_tokens(self) -> None:
        """Test hook: invalidate all previously issued access tokens."""
        self._tokens.clear()

    def inject_transient_errors(self, *statuses: int) -> None:
        """Test hook: make the next N Drive API requests fail with given status codes."""
        self._transient_errors.extend(statuses)

    def _load_state(self) -> None:
        if self.meta_path.is_file():
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._files = raw.get("files", {})

    def _save_state(self) -> None:
        _atomic_write_json(self.meta_path, {"files": self._files})

    def _blob_path(self, file_id: str) -> Path:
        return self.blobs_dir / file_id

    def _check_auth(self, request: web.Request, *, required: bool = True) -> web.Response | None:
        auth = request.headers.get("Authorization", "")
        if not auth:
            if not required:
                return None
            return web.json_response(
                {"error": {"code": 401, "message": "Missing Authorization"}}, status=401
            )
        if not auth.startswith("Bearer "):
            return web.json_response(
                {"error": {"code": 401, "message": "Invalid Authorization scheme"}}, status=401
            )
        token = auth[7:].strip()
        exp = self._tokens.get(token)
        if exp is None or exp <= time.monotonic():
            return web.json_response(
                {"error": {"code": 401, "message": "Invalid Credentials"}}, status=401
            )
        return None

    def _maybe_transient_error(self) -> web.Response | None:
        if self._transient_errors:
            code = self._transient_errors.pop(0)
            return web.json_response(
                {"error": {"code": code, "message": f"Injected transient {code}"}},
                status=code,
            )
        return None

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=256 * 1024 * 1024)
        app.router.add_post("/token", self.handle_token)
        app.router.add_get("/drive/v3/files", self.handle_list_files)
        app.router.add_post("/drive/v3/files", self.handle_create_metadata)
        app.router.add_get("/drive/v3/files/{file_id}", self.handle_get_file)
        app.router.add_delete("/drive/v3/files/{file_id}", self.handle_delete_file)
        app.router.add_post("/upload/drive/v3/files", self.handle_upload)
        app.router.add_patch("/upload/drive/v3/files/{file_id}", self.handle_upload)
        app.router.add_put("/upload/drive/v3/sessions/{upload_id}", self.handle_resumable_put)
        return app

    async def start(self) -> "FakeGDriveServer":
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self._requested_port)
        await self._site.start()
        sockets = self._site._server.sockets  # type: ignore[union-attr]
        if sockets:
            self.port = int(sockets[0].getsockname()[1])
        return self

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def __aenter__(self) -> "FakeGDriveServer":
        return await self.start()

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def handle_token(self, request: web.Request) -> web.Response:
        if request.content_type == "application/json":
            data = await request.json()
        else:
            data = dict(await request.post())
        if (
            data.get("client_id") != self.valid_client_id
            or data.get("client_secret") != self.valid_client_secret
        ):
            return web.json_response({"error": "invalid_client"}, status=401)
        grant = data.get("grant_type", "")
        if grant == "refresh_token":
            if data.get("refresh_token") != self.valid_refresh_token:
                return web.json_response({"error": "invalid_grant"}, status=400)
        elif grant == "authorization_code":
            if not data.get("code"):
                return web.json_response({"error": "invalid_grant"}, status=400)
        else:
            return web.json_response({"error": "unsupported_grant_type"}, status=400)

        tok = f"ya29.fake_{secrets.token_hex(12)}"
        self._tokens[tok] = time.monotonic() + self.token_ttl_s
        self.token_refresh_count += 1
        return web.json_response(
            {
                "access_token": tok,
                "expires_in": int(self.token_ttl_s),
                "token_type": "Bearer",
                **({"refresh_token": self.valid_refresh_token} if grant == "authorization_code" else {}),
            }
        )

    async def handle_list_files(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if err := self._maybe_transient_error():
            return err
        q = request.query.get("q", "")
        matches = [dict(item) for item in self._files.values() if _match_drive_query(item, q)]
        return web.json_response({"files": matches})

    async def handle_create_metadata(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if err := self._maybe_transient_error():
            return err
        body = await request.json()
        file_id = f"gd_{secrets.token_hex(8)}"
        item = {
            "id": file_id,
            "name": body.get("name", "untitled"),
            "mimeType": body.get("mimeType", "application/octet-stream"),
            "parents": body.get("parents") or ["root"],
            "size": "0",
            "trashed": False,
        }
        self._files[file_id] = item
        self._save_state()
        return web.json_response(item)

    async def handle_get_file(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if err := self._maybe_transient_error():
            return err
        file_id = request.match_info["file_id"]
        item = self._files.get(file_id)
        if not item or item.get("trashed"):
            return web.json_response({"error": {"code": 404, "message": "Not Found"}}, status=404)

        if request.query.get("alt") != "media":
            return web.json_response(item)

        self.download_count += 1
        blob_path = self._blob_path(file_id)
        data = blob_path.read_bytes() if blob_path.is_file() else b""
        total_size = len(data)
        mime = item.get("mimeType") or "application/octet-stream"
        parsed = parse_range_header(request.headers.get("Range"), total_size)

        if parsed == "invalid":
            return web.Response(
                status=416,
                headers={
                    "Content-Range": f"bytes */{total_size}",
                    "Accept-Ranges": "bytes",
                },
            )
        if parsed is None:
            return web.Response(
                status=200,
                body=data,
                headers={
                    "Content-Type": mime,
                    "Content-Length": str(total_size),
                    "Accept-Ranges": "bytes",
                },
            )
        start, end = parsed
        chunk = data[start : end + 1]
        return web.Response(
            status=206,
            body=chunk,
            headers={
                "Content-Type": mime,
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Accept-Ranges": "bytes",
            },
        )

    async def handle_delete_file(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if err := self._maybe_transient_error():
            return err
        file_id = request.match_info["file_id"]
        if file_id not in self._files:
            return web.json_response({"error": {"code": 404, "message": "Not Found"}}, status=404)
        self._files.pop(file_id, None)
        self._blob_path(file_id).unlink(missing_ok=True)
        self._save_state()
        self.delete_count += 1
        return web.Response(status=204)

    async def handle_upload(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request):
            return err
        if err := self._maybe_transient_error():
            return err

        target_id = request.match_info.get("file_id", "")
        if target_id and target_id not in self._files:
            return web.json_response({"error": {"code": 404, "message": "Not Found"}}, status=404)

        upload_type = request.query.get("uploadType", "media")
        if upload_type == "media":
            data = await request.read()
            mime = request.headers.get("Content-Type", "application/octet-stream")
            meta = {"mimeType": mime}
            return self._commit_upload(target_id=target_id, meta=meta, data=data)

        if upload_type == "multipart":
            body = await request.read()
            meta, data, media_mime = _parse_multipart_related(
                request.headers.get("Content-Type", ""), body
            )
            meta.setdefault("mimeType", media_mime)
            return self._commit_upload(target_id=target_id, meta=meta, data=data)

        if upload_type == "resumable":
            raw = await request.read()
            meta = json.loads(raw.decode("utf-8")) if raw else {}
            if "X-Upload-Content-Type" in request.headers:
                meta.setdefault("mimeType", request.headers["X-Upload-Content-Type"])
            upload_id = f"up_{secrets.token_hex(10)}"
            tmp_file = self.sessions_dir / f"{upload_id}.bin"
            tmp_file.write_bytes(b"")
            self._upload_sessions[upload_id] = {
                "target_id": target_id,
                "meta": meta,
                "tmp_path": str(tmp_file),
                "bytes_written": 0,
            }
            location = f"{self.upload_base}/sessions/{upload_id}"
            return web.Response(
                status=200,
                headers={"Location": location, "X-GUploader-UploadID": upload_id},
            )

        return web.json_response({"error": f"Unsupported uploadType {upload_type!r}"}, status=400)

    def _commit_upload(self, *, target_id: str, meta: dict[str, Any], data: bytes) -> web.Response:
        file_id = target_id or f"gd_{secrets.token_hex(8)}"
        existing = self._files.get(file_id, {})
        item = {
            "id": file_id,
            "name": meta.get("name") or existing.get("name") or file_id,
            "mimeType": meta.get("mimeType") or existing.get("mimeType") or "application/octet-stream",
            "parents": meta.get("parents") or existing.get("parents") or ["root"],
            "size": str(len(data)),
            "trashed": False,
        }
        self._blob_path(file_id).write_bytes(data)
        self._files[file_id] = item
        self._save_state()
        self.upload_count += 1
        return web.json_response(item)

    async def handle_resumable_put(self, request: web.Request) -> web.Response:
        if err := self._check_auth(request, required=False):
            return err
        if err := self._maybe_transient_error():
            return err

        upload_id = request.match_info["upload_id"]
        sess = self._upload_sessions.get(upload_id)
        if not sess:
            return web.json_response({"error": {"code": 404, "message": "Upload expired"}}, status=404)

        self.resumable_put_count += 1
        chunk = await request.read()
        tmp_path = Path(sess["tmp_path"])
        cr = request.headers.get("Content-Range", "").strip()

        if not cr:
            tmp_path.write_bytes(chunk)
            data = tmp_path.read_bytes()
            tmp_path.unlink(missing_ok=True)
            self._upload_sessions.pop(upload_id, None)
            return self._commit_upload(target_id=sess["target_id"], meta=sess["meta"], data=data)

        # bytes */total  OR  bytes start-end/*  OR  bytes start-end/total
        m_star = re.match(r"^bytes\s+\*/(\d+)$", cr, flags=re.IGNORECASE)
        if m_star:
            total = int(m_star.group(1))
            data = tmp_path.read_bytes()[:total]
            tmp_path.unlink(missing_ok=True)
            self._upload_sessions.pop(upload_id, None)
            return self._commit_upload(target_id=sess["target_id"], meta=sess["meta"], data=data)

        m_range = re.match(r"^bytes\s+(\d+)-(\d+)/(\*|\d+)$", cr, flags=re.IGNORECASE)
        if not m_range:
            return web.json_response({"error": f"Invalid Content-Range: {cr!r}"}, status=400)

        start = int(m_range.group(1))
        end = int(m_range.group(2))
        total_raw = m_range.group(3)
        expected_len = end - start + 1
        if len(chunk) != expected_len:
            return web.json_response(
                {"error": f"Chunk length {len(chunk)} != range {expected_len}"},
                status=400,
            )

        with tmp_path.open("r+b") as f:
            f.seek(start)
            f.write(chunk)
        sess["bytes_written"] = max(sess["bytes_written"], end + 1)

        if total_raw == "*" or (end + 1) < int(total_raw):
            return web.Response(
                status=308,
                headers={"Range": f"bytes=0-{end}"},
            )

        total = int(total_raw)
        data = tmp_path.read_bytes()[:total]
        tmp_path.unlink(missing_ok=True)
        self._upload_sessions.pop(upload_id, None)
        return self._commit_upload(target_id=sess["target_id"], meta=sess["meta"], data=data)


async def _serve_forever(host: str, port: int, root_dir: str) -> None:
    srv = FakeGDriveServer(root_dir, host=host, port=port)
    await srv.start()
    print(f"FakeGDriveServer listening on {srv.base_url} (dir={srv.root_dir})", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await srv.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local FakeGDriveServer for testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--dir", required=True, help="Directory for persisted fake Drive blobs/metadata")
    args = parser.parse_args()
    asyncio.run(_serve_forever(args.host, args.port, args.dir))


if __name__ == "__main__":
    main()
