"""Pure aiohttp Google Drive API v3 ArchiveStore implementation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, AsyncIterator

import aiohttp

from .storage import (
    BlobMeta,
    _norm_ext,
    _safe_segment,
    atomic_write_bytes,
    guess_mime,
    verify_sqlite_integrity,
)

log = logging.getLogger("gate.gdrive")

DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_API_BASE = "https://www.googleapis.com/drive/v3"
DEFAULT_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
RESUMABLE_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB (16 * 256 KiB, Drive API compliant)
EARLY_REFRESH_SECONDS = 120.0
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class GDriveError(RuntimeError):
    """Error raised for unrecoverable Google Drive API failures."""


def _escape_q(val: str) -> str:
    return val.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveStore:
    """Google Drive v3 ArchiveStore backed by OAuth refresh token and local input cache."""

    backend_name: str = "gdrive"

    def __init__(
        self,
        oauth_file: str | Path,
        root_folder_id: str = "",
        cache_dir: str | Path = "./data/archive",
        archive_max_gb: float = 20.0,
        *,
        max_retries: int = 4,
    ):
        self.oauth_file = Path(oauth_file)
        self._configured_root_id = (root_folder_id or "").strip()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.archive_max_gb = float(archive_max_gb)
        self._max_retries = max(1, int(max_retries))

        self._oauth_cfg: dict[str, str] | None = None
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

        self._root_folder_id: str = self._configured_root_id
        self._folder_cache: dict[tuple[str, str], str] = {}
        self._folder_lock = asyncio.Lock()

        self._session: aiohttp.ClientSession | None = None
        self.last_backup_at: float | None = None
        self.last_error: str = ""

    def _load_oauth(self) -> dict[str, str]:
        if self._oauth_cfg is not None:
            return self._oauth_cfg
        raw = self.oauth_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise GDriveError("OAuth file must contain a JSON object")
        for req_key in ("client_id", "client_secret", "refresh_token"):
            if not data.get(req_key):
                raise GDriveError(f"OAuth JSON missing required field: {req_key}")
        self._oauth_cfg = {
            "client_id": str(data["client_id"]),
            "client_secret": str(data["client_secret"]),
            "refresh_token": str(data["refresh_token"]),
            "token_uri": str(data.get("token_uri") or DEFAULT_TOKEN_URI).rstrip("/"),
            "api_base": str(data.get("api_base") or DEFAULT_API_BASE).rstrip("/"),
            "upload_base": str(data.get("upload_base") or DEFAULT_UPLOAD_BASE).rstrip("/"),
            "root_folder_id": str(data.get("root_folder_id") or "").strip(),
        }
        if not self._root_folder_id and self._oauth_cfg["root_folder_id"]:
            self._root_folder_id = self._oauth_cfg["root_folder_id"]
        return self._oauth_cfg

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=120)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get_access_token(self, failed_token: str | None = None) -> str:
        """Return a valid access token, refreshing early (120s) or on 401 under lock."""
        now = time.monotonic()
        if (
            failed_token is None
            and self._access_token
            and now < (self._token_expires_at - EARLY_REFRESH_SECONDS)
        ):
            return self._access_token

        async with self._token_lock:
            now = time.monotonic()
            # Another waiter may have already refreshed the failed token.
            if (
                self._access_token
                and self._access_token != failed_token
                and now < (self._token_expires_at - EARLY_REFRESH_SECONDS)
            ):
                return self._access_token

            cfg = self._load_oauth()
            sess = await self._http()
            payload = {
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": cfg["refresh_token"],
                "grant_type": "refresh_token",
            }
            last_exc: Exception | None = None
            for attempt in range(self._max_retries):
                try:
                    async with sess.post(
                        cfg["token_uri"],
                        data=payload,
                        timeout=aiohttp.ClientTimeout(total=20, sock_connect=10, sock_read=15),
                    ) as resp:
                        body_text = await resp.text()
                        if resp.status == 200:
                            tok = json.loads(body_text)
                            access_token = str(tok.get("access_token") or "")
                            if not access_token:
                                raise GDriveError("Token response did not contain access_token")
                            expires_in = float(tok.get("expires_in") or 3600.0)
                            self._access_token = access_token
                            self._token_expires_at = time.monotonic() + expires_in
                            return access_token
                        if resp.status in RETRYABLE_STATUSES and attempt + 1 < self._max_retries:
                            delay = self._retry_delay(attempt, resp.headers.get("Retry-After"))
                            await asyncio.sleep(delay)
                            continue
                        raise GDriveError(f"OAuth token refresh failed with HTTP {resp.status}")
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exc = e
                    if attempt + 1 < self._max_retries:
                        await asyncio.sleep(self._retry_delay(attempt))
                        continue
            raise GDriveError(f"OAuth token refresh failed: {type(last_exc).__name__}")

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                val = float(retry_after)
                if val > 0:
                    return min(val, 30.0)
            except ValueError:
                pass
        base = min(0.25 * (2 ** attempt), 8.0)
        return base + random.uniform(0.0, 0.25)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        data: Any = None,
        json_body: dict[str, Any] | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
        allow_404: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute an authenticated Drive API request with single 401 refresh and 429/5xx backoff."""
        sess = await self._http()
        req_timeout = timeout or aiohttp.ClientTimeout(total=60, sock_connect=15, sock_read=45)
        refreshed_after_401 = False
        failed_token: str | None = None

        for attempt in range(self._max_retries):
            token = await self._get_access_token(failed_token=failed_token)
            failed_token = None
            req_headers = dict(headers or {})
            req_headers["Authorization"] = f"Bearer {token}"

            try:
                async with sess.request(
                    method,
                    url,
                    headers=req_headers,
                    params=params,
                    data=data,
                    json=json_body,
                    timeout=req_timeout,
                ) as resp:
                    resp_headers = dict(resp.headers)
                    if resp.status == 401 and not refreshed_after_401:
                        await resp.release()
                        refreshed_after_401 = True
                        failed_token = token
                        continue
                    if resp.status in RETRYABLE_STATUSES and attempt + 1 < self._max_retries:
                        await resp.release()
                        await asyncio.sleep(self._retry_delay(attempt, resp_headers.get("Retry-After")))
                        continue
                    body = await resp.read()
                    if resp.status == 404 and allow_404:
                        return 404, resp_headers, body
                    if resp.status >= 400 and resp.status != 308:
                        raise GDriveError(f"Drive API {method} failed with HTTP {resp.status}")
                    return resp.status, resp_headers, body
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep(self._retry_delay(attempt))
                    continue
                raise GDriveError(f"Drive API {method} network error: {type(e).__name__}") from e

        raise GDriveError(f"Drive API {method} exhausted retries")

    async def _find_item(
        self, parent_id: str, name: str, *, folder_only: bool = False
    ) -> dict[str, Any] | None:
        cfg = self._load_oauth()
        clauses = [
            f"'{_escape_q(parent_id)}' in parents",
            f"name = '{_escape_q(name)}'",
            "trashed = false",
        ]
        if folder_only:
            clauses.append(f"mimeType = '{FOLDER_MIME}'")
        q = " and ".join(clauses)
        _status, _hdrs, body = await self._request(
            "GET",
            f"{cfg['api_base']}/files",
            params={
                "q": q,
                "fields": "files(id,name,mimeType,size)",
                "pageSize": "10",
            },
        )
        parsed = json.loads(body.decode("utf-8"))
        files = parsed.get("files") or []
        return files[0] if files else None

    async def _create_folder(self, parent_id: str, name: str) -> str:
        cfg = self._load_oauth()
        meta: dict[str, Any] = {
            "name": name,
            "mimeType": FOLDER_MIME,
        }
        if parent_id and parent_id != "root":
            meta["parents"] = [parent_id]
        elif parent_id == "root":
            meta["parents"] = ["root"]
        _status, _hdrs, body = await self._request(
            "POST",
            f"{cfg['api_base']}/files",
            json_body=meta,
        )
        created = json.loads(body.decode("utf-8"))
        return str(created["id"])

    async def _ensure_root_folder(self) -> str:
        self._load_oauth()
        if self._root_folder_id:
            return self._root_folder_id
        async with self._folder_lock:
            if self._root_folder_id:
                return self._root_folder_id
            found = await self._find_item("root", "comfy-colab-kit", folder_only=True)
            if found:
                self._root_folder_id = str(found["id"])
            else:
                self._root_folder_id = await self._create_folder("root", "comfy-colab-kit")
            return self._root_folder_id

    async def _ensure_folder_path(self, parts: list[str]) -> str:
        parent_id = await self._ensure_root_folder()
        for part in parts:
            key = (parent_id, part)
            cached = self._folder_cache.get(key)
            if cached:
                parent_id = cached
                continue
            async with self._folder_lock:
                cached = self._folder_cache.get(key)
                if cached:
                    parent_id = cached
                    continue
                found = await self._find_item(parent_id, part, folder_only=True)
                if found:
                    folder_id = str(found["id"])
                else:
                    folder_id = await self._create_folder(parent_id, part)
                self._folder_cache[key] = folder_id
                parent_id = folder_id
        return parent_id

    async def _resolve_storage_key_id(self, storage_key: str) -> str:
        """Walk Drive folders matching storage_key (e.g. archive/prof/2026-11/job/file)."""
        parts = [p for p in storage_key.strip("/").split("/") if p]
        if not parts:
            return ""
        parent_id = await self._ensure_root_folder()
        for folder in parts[:-1]:
            key = (parent_id, folder)
            fid = self._folder_cache.get(key)
            if not fid:
                found = await self._find_item(parent_id, folder, folder_only=True)
                if not found:
                    return ""
                fid = str(found["id"])
                self._folder_cache[key] = fid
            parent_id = fid
        item = await self._find_item(parent_id, parts[-1], folder_only=False)
        return str(item["id"]) if item else ""

    @staticmethod
    def _build_multipart_body(
        metadata: dict[str, Any], data: bytes, mime: str
    ) -> tuple[str, bytes]:
        boundary = f"kit_{os.urandom(12).hex()}"
        meta_json = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
        parts = [
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8"),
            meta_json,
            f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"),
            data,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        return f"multipart/related; boundary={boundary}", b"".join(parts)

    async def _upload_bytes(
        self,
        *,
        name: str,
        parent_id: str,
        data: bytes,
        mime: str,
        existing_id: str = "",
    ) -> str:
        cfg = self._load_oauth()
        meta: dict[str, Any] = {"name": name, "mimeType": mime}
        if not existing_id:
            meta["parents"] = [parent_id]
        ctype, body = self._build_multipart_body(meta, data, mime)
        if existing_id:
            url = f"{cfg['upload_base']}/files/{urllib.parse.quote(existing_id, safe='')}"
            method = "PATCH"
        else:
            url = f"{cfg['upload_base']}/files"
            method = "POST"
        _status, _hdrs, resp_body = await self._request(
            method,
            url,
            params={"uploadType": "multipart"},
            headers={"Content-Type": ctype},
            data=body,
            timeout=aiohttp.ClientTimeout(total=120, sock_connect=15, sock_read=90),
        )
        return str(json.loads(resp_body.decode("utf-8"))["id"])

    def _cache_input_path(self, sha256: str, ext: str, mime: str = "") -> tuple[str, Path]:
        safe_sha = _safe_segment(sha256, "00")
        norm_ext = _norm_ext(ext, mime)
        prefix = safe_sha[:2] if len(safe_sha) >= 2 else safe_sha.ljust(2, "0")
        key = f"inputs/{prefix}/{safe_sha}{norm_ext}"
        return key, self.cache_dir / key

    def _prune_input_cache_if_needed(self) -> None:
        if self.archive_max_gb <= 0:
            return
        inputs_root = self.cache_dir / "inputs"
        if not inputs_root.is_dir():
            return
        max_bytes = int(self.archive_max_gb * (1024 ** 3))
        files = [
            (p.stat().st_mtime, p.stat().st_size, p)
            for p in inputs_root.rglob("*")
            if p.is_file() and not p.name.startswith(".tmp-")
        ]
        total = sum(sz for _, sz, _ in files)
        if total <= max_bytes:
            return
        files.sort(key=lambda x: x[0])
        for _mtime, sz, p in files:
            if total <= max_bytes:
                break
            try:
                p.unlink()
                total -= sz
            except OSError:
                continue

    async def put_input(self, sha256: str, ext: str, data: bytes, mime: str) -> BlobMeta:
        digest = sha256 or hashlib.sha256(data).hexdigest()
        key, cache_path = self._cache_input_path(digest, ext, mime)
        filename = cache_path.name
        prefix = cache_path.parent.name

        if not cache_path.is_file() or cache_path.stat().st_size != len(data):
            await asyncio.to_thread(atomic_write_bytes, cache_path, data)
            await asyncio.to_thread(self._prune_input_cache_if_needed)

        folder_id = await self._ensure_folder_path(["inputs", prefix])
        existing = await self._find_item(folder_id, filename, folder_only=False)
        if existing:
            remote_id = str(existing["id"])
        else:
            effective_mime = mime or guess_mime(filename)
            if len(data) <= RESUMABLE_CHUNK_SIZE:
                remote_id = await self._upload_bytes(
                    name=filename,
                    parent_id=folder_id,
                    data=data,
                    mime=effective_mime,
                )
            else:
                async def _single_iter() -> AsyncIterator[bytes]:
                    yield data

                meta = await self._resumable_upload(
                    parent_id=folder_id,
                    filename=filename,
                    stream=_single_iter(),
                    total_size=len(data),
                    mime=effective_mime,
                )
                remote_id = str(meta["id"])

        return BlobMeta(
            storage_backend="gdrive",
            storage_key=key,
            remote_id=remote_id,
            size_bytes=len(data),
            sha256=digest,
            mime=mime or guess_mime(filename),
        )

    async def get_input(self, sha256: str, ext: str, remote_id: str = "") -> bytes | None:
        _key, cache_path = self._cache_input_path(sha256, ext)
        if cache_path.is_file():
            return await asyncio.to_thread(cache_path.read_bytes)

        target_id = (remote_id or "").strip()
        if not target_id:
            prefix = cache_path.parent.name
            folder_id = await self._ensure_folder_path(["inputs", prefix])
            existing = await self._find_item(folder_id, cache_path.name, folder_only=False)
            if not existing:
                return None
            target_id = str(existing["id"])

        cfg = self._load_oauth()
        status, _hdrs, body = await self._request(
            "GET",
            f"{cfg['api_base']}/files/{urllib.parse.quote(target_id, safe='')}",
            params={"alt": "media"},
            allow_404=True,
            timeout=aiohttp.ClientTimeout(total=180, sock_connect=15, sock_read=90),
        )
        if status == 404:
            return None
        await asyncio.to_thread(atomic_write_bytes, cache_path, body)
        await asyncio.to_thread(self._prune_input_cache_if_needed)
        return body

    async def _resumable_upload(
        self,
        *,
        parent_id: str,
        filename: str,
        stream: AsyncIterator[bytes],
        total_size: int | None,
        mime: str,
        existing_id: str = "",
        hasher: Any | None = None,
        counter: list[int] | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_oauth()
        meta: dict[str, Any] = {"name": filename, "mimeType": mime}
        init_headers: dict[str, str] = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime,
        }
        if total_size is not None:
            init_headers["X-Upload-Content-Length"] = str(total_size)

        if existing_id:
            init_url = f"{cfg['upload_base']}/files/{urllib.parse.quote(existing_id, safe='')}"
            init_method = "PATCH"
        else:
            meta["parents"] = [parent_id]
            init_url = f"{cfg['upload_base']}/files"
            init_method = "POST"

        _status, resp_hdrs, _body = await self._request(
            init_method,
            init_url,
            params={"uploadType": "resumable"},
            headers=init_headers,
            json_body=meta,
            timeout=aiohttp.ClientTimeout(total=30, sock_connect=15, sock_read=20),
        )
        location = resp_hdrs.get("Location") or resp_hdrs.get("location")
        if not location:
            raise GDriveError("Resumable upload initiation did not return Location header")

        async def _put_chunk(chunk_bytes: bytes, content_range: str) -> tuple[int, dict[str, str], bytes]:
            return await self._request(
                "PUT",
                location,
                headers={
                    "Content-Length": str(len(chunk_bytes)),
                    "Content-Range": content_range,
                },
                data=chunk_bytes,
                timeout=aiohttp.ClientTimeout(total=300, sock_connect=15, sock_read=180),
            )

        buf = bytearray()
        offset = 0
        final_resp: dict[str, Any] | None = None

        async for raw_chunk in stream:
            if not raw_chunk:
                continue
            if hasher is not None:
                hasher.update(raw_chunk)
            if counter is not None:
                counter[0] += len(raw_chunk)
            buf.extend(raw_chunk)

            # Flush 4 MiB chunks while keeping at least 1 byte for EOF when total is unknown
            # (or when this chunk would reach total_size) so EOF always sends the exact total.
            while len(buf) > RESUMABLE_CHUNK_SIZE or (
                total_size is not None
                and len(buf) >= RESUMABLE_CHUNK_SIZE
                and (offset + RESUMABLE_CHUNK_SIZE) < total_size
            ):
                piece = bytes(buf[:RESUMABLE_CHUNK_SIZE])
                del buf[:RESUMABLE_CHUNK_SIZE]
                end = offset + len(piece) - 1
                total_part = str(total_size) if (total_size is not None and end + 1 < total_size) else "*"
                status, _h, body = await _put_chunk(piece, f"bytes {offset}-{end}/{total_part}")
                offset += len(piece)
                if status in (200, 201):
                    final_resp = json.loads(body.decode("utf-8"))

        # Flush final buffer (or handle 0-byte / exact-boundary finalization)
        if final_resp is None:
            if len(buf) > 0:
                piece = bytes(buf)
                total_len = offset + len(piece)
                end = total_len - 1
                status, _h, body = await _put_chunk(piece, f"bytes {offset}-{end}/{total_len}")
                offset = total_len
            else:
                # 0-byte upload or exact chunk boundary already sent with /*
                status, _h, body = await _put_chunk(b"", f"bytes */{offset}")
            if status not in (200, 201):
                raise GDriveError(f"Resumable upload did not finalize cleanly (HTTP {status})")
            final_resp = json.loads(body.decode("utf-8"))

        return final_resp

    async def put_output_stream(
        self,
        profile_id: str,
        yyyy_mm: str,
        job_id: str,
        filename: str,
        stream: AsyncIterator[bytes],
        total_size: int | None,
        mime: str,
    ) -> BlobMeta:
        p = _safe_segment(profile_id, "default")
        ym = _safe_segment(yyyy_mm, "1970-01")
        j = _safe_segment(job_id, "job")
        fn = _safe_segment(filename, "output.bin")
        key = f"archive/{p}/{ym}/{j}/{fn}"
        effective_mime = mime or guess_mime(fn)

        folder_id = await self._ensure_folder_path(["archive", p, ym, j])
        hasher = hashlib.sha256()
        counter = [0]
        created = await self._resumable_upload(
            parent_id=folder_id,
            filename=fn,
            stream=stream,
            total_size=total_size,
            mime=effective_mime,
            hasher=hasher,
            counter=counter,
        )
        return BlobMeta(
            storage_backend="gdrive",
            storage_key=key,
            remote_id=str(created["id"]),
            size_bytes=counter[0],
            sha256=hasher.hexdigest(),
            mime=effective_mime,
        )

    async def put_job_manifest(
        self, profile_id: str, yyyy_mm: str, job_id: str, manifest: dict
    ) -> str:
        p = _safe_segment(profile_id, "default")
        ym = _safe_segment(yyyy_mm, "1970-01")
        j = _safe_segment(job_id, "job")
        key = f"archive/{p}/{ym}/{j}/job.json"
        folder_id = await self._ensure_folder_path(["archive", p, ym, j])
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        existing = await self._find_item(folder_id, "job.json", folder_only=False)
        await self._upload_bytes(
            name="job.json",
            parent_id=folder_id,
            data=payload,
            mime="application/json",
            existing_id=str(existing["id"]) if existing else "",
        )
        return key

    async def open_output_stream(
        self, storage_key: str, remote_id: str, range_header: str | None = None
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        target_id = (remote_id or "").strip()
        if not target_id and storage_key:
            target_id = await self._resolve_storage_key_id(storage_key)
        if not target_id:
            raise FileNotFoundError(storage_key or "missing remote_id")

        cfg = self._load_oauth()
        url = f"{cfg['api_base']}/files/{urllib.parse.quote(target_id, safe='')}"
        sess = await self._http()
        req_timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=120)
        refreshed_after_401 = False
        failed_token: str | None = None

        for attempt in range(self._max_retries):
            token = await self._get_access_token(failed_token=failed_token)
            failed_token = None
            req_headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
            if range_header:
                req_headers["Range"] = range_header

            try:
                resp = await sess.get(
                    url,
                    params={"alt": "media"},
                    headers=req_headers,
                    timeout=req_timeout,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep(self._retry_delay(attempt))
                    continue
                raise GDriveError(f"Drive stream open failed: {type(e).__name__}") from e

            if resp.status == 401 and not refreshed_after_401:
                await resp.release()
                refreshed_after_401 = True
                failed_token = token
                continue
            if resp.status in RETRYABLE_STATUSES and attempt + 1 < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                await resp.release()
                await asyncio.sleep(self._retry_delay(attempt, retry_after))
                continue
            if resp.status == 404:
                await resp.release()
                raise FileNotFoundError(storage_key or target_id)
            if resp.status >= 400 and resp.status != 416:
                status = resp.status
                await resp.release()
                raise GDriveError(f"Drive media stream failed with HTTP {status}")

            out_headers: dict[str, str] = {
                "Accept-Ranges": resp.headers.get("Accept-Ranges", "bytes"),
                "Content-Type": resp.headers.get(
                    "Content-Type",
                    guess_mime(storage_key) if storage_key else "application/octet-stream",
                ),
            }
            if "Content-Length" in resp.headers:
                out_headers["Content-Length"] = resp.headers["Content-Length"]
            if "Content-Range" in resp.headers:
                out_headers["Content-Range"] = resp.headers["Content-Range"]

            async def _stream_body(response: aiohttp.ClientResponse = resp) -> AsyncIterator[bytes]:
                try:
                    async for chunk in response.content.iter_chunked(65536):
                        if chunk:
                            yield chunk
                finally:
                    await response.release()

            return resp.status, out_headers, _stream_body()

        raise GDriveError("Drive media stream exhausted retries")

    async def delete_blob(self, storage_key: str, remote_id: str = "") -> bool:
        target_id = (remote_id or "").strip()
        if not target_id and storage_key:
            target_id = await self._resolve_storage_key_id(storage_key)
        deleted_remote = False
        if target_id:
            cfg = self._load_oauth()
            status, _hdrs, _body = await self._request(
                "DELETE",
                f"{cfg['api_base']}/files/{urllib.parse.quote(target_id, safe='')}",
                allow_404=True,
            )
            deleted_remote = status != 404
        if storage_key:
            cached = (self.cache_dir / storage_key.lstrip("/")).resolve()
            try:
                cached.relative_to(self.cache_dir.resolve())
                if cached.is_file():
                    cached.unlink()
            except ValueError:
                pass
        return deleted_remote

    async def backup_db(self, db_snapshot_path: Path, meta: dict) -> bool:
        try:
            src = Path(db_snapshot_path)
            if not src.is_file():
                raise FileNotFoundError(f"Snapshot missing: {src}")
            db_folder_id = await self._ensure_folder_path(["db"])

            db_bytes = await asyncio.to_thread(src.read_bytes)
            existing_db = await self._find_item(db_folder_id, "gate.db", folder_only=False)
            existing_db_id = str(existing_db["id"]) if existing_db else ""

            if len(db_bytes) <= RESUMABLE_CHUNK_SIZE:
                await self._upload_bytes(
                    name="gate.db",
                    parent_id=db_folder_id,
                    data=db_bytes,
                    mime="application/x-sqlite3",
                    existing_id=existing_db_id,
                )
            else:
                async def _db_iter() -> AsyncIterator[bytes]:
                    for i in range(0, len(db_bytes), RESUMABLE_CHUNK_SIZE):
                        yield db_bytes[i : i + RESUMABLE_CHUNK_SIZE]

                await self._resumable_upload(
                    parent_id=db_folder_id,
                    filename="gate.db",
                    stream=_db_iter(),
                    total_size=len(db_bytes),
                    mime="application/x-sqlite3",
                    existing_id=existing_db_id,
                )

            meta_bytes = json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8")
            existing_meta = await self._find_item(db_folder_id, "gate.db.meta.json", folder_only=False)
            await self._upload_bytes(
                name="gate.db.meta.json",
                parent_id=db_folder_id,
                data=meta_bytes,
                mime="application/json",
                existing_id=str(existing_meta["id"]) if existing_meta else "",
            )

            self.last_backup_at = time.time()
            self.last_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.warning("Google Drive DB backup failed: %s", e)
            return False

    async def restore_db_if_missing(self, target_db_path: Path) -> bool:
        target = Path(target_db_path)
        if target.exists():
            return False
        try:
            db_folder_id = await self._ensure_folder_path(["db"])
            found = await self._find_item(db_folder_id, "gate.db", folder_only=False)
            if not found:
                return False
            file_id = str(found["id"])
            cfg = self._load_oauth()
            status, _hdrs, body = await self._request(
                "GET",
                f"{cfg['api_base']}/files/{urllib.parse.quote(file_id, safe='')}",
                params={"alt": "media"},
                allow_404=True,
                timeout=aiohttp.ClientTimeout(total=180, sock_connect=15, sock_read=120),
            )
            if status == 404 or not body:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".restore-", suffix=".db.tmp", dir=str(target.parent)
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                if not await asyncio.to_thread(verify_sqlite_integrity, tmp_path):
                    tmp_path.unlink(missing_ok=True)
                    return False
                os.replace(tmp_path, target)
                return True
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        except Exception as e:  # noqa: BLE001
            log.warning("Google Drive DB restore failed: %s", e)
            return False

    async def status_info(self) -> dict[str, Any]:
        return {
            "backend": "gdrive",
            "root_folder_id": self._root_folder_id,
            "last_backup_at": self.last_backup_at,
            "last_error": self.last_error,
        }
