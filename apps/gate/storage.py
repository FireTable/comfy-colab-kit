"""Storage abstraction (ArchiveStore), local disk backend, thumbnails, and factory."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, runtime_checkable

log = logging.getLogger("gate.storage")

# Ensure common media extensions resolve consistently across platforms.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
}


def _safe_segment(val: str, fallback: str = "item") -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", (val or "").strip()).strip(".")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    return cleaned


def _norm_ext(ext: str, mime: str = "") -> str:
    raw = (ext or "").strip()
    if raw:
        raw = raw if raw.startswith(".") else f".{raw}"
        return _SAFE_SEGMENT_RE.sub("", raw) or ".bin"
    if mime:
        base_mime = mime.split(";", 1)[0].strip().lower()
        if base_mime in _MIME_TO_EXT:
            return _MIME_TO_EXT[base_mime]
        guessed = mimetypes.guess_extension(base_mime)
        if guessed:
            return guessed
    return ""


def guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or fallback


def parse_range_header(range_header: str | None, total_size: int) -> tuple[int, int] | None | str:
    """Parse HTTP Range header.

    Returns:
        None if no Range header was given.
        "invalid" if the Range header is unsatisfiable / malformed (caller should return 416).
        (start, end) inclusive byte range when valid.
    """
    if not range_header:
        return None
    spec = range_header.strip()
    if not spec.lower().startswith("bytes="):
        return "invalid"
    spec = spec[6:].strip()
    if "," in spec or "-" not in spec:
        return "invalid"
    start_s, end_s = spec.split("-", 1)
    start_s, end_s = start_s.strip(), end_s.strip()
    if total_size <= 0:
        return "invalid"
    try:
        if start_s == "":
            # Suffix range: bytes=-N
            suffix = int(end_s)
            if suffix <= 0:
                return "invalid"
            start = max(0, total_size - suffix)
            end = total_size - 1
        else:
            start = int(start_s)
            if start < 0 or start >= total_size:
                return "invalid"
            if end_s == "":
                end = total_size - 1
            else:
                end = int(end_s)
                if end < start:
                    return "invalid"
                end = min(end, total_size - 1)
    except ValueError:
        return "invalid"
    return (start, end)


def verify_sqlite_integrity(db_path: Path) -> bool:
    """Verify a SQLite database file using PRAGMA quick_check."""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        row = conn.execute("PRAGMA quick_check;").fetchone()
        return bool(row and str(row[0]).lower() == "ok")
    except Exception:  # noqa: BLE001
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(dest.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class BlobMeta:
    storage_backend: str
    storage_key: str
    remote_id: str
    size_bytes: int
    sha256: str
    mime: str = "application/octet-stream"


@runtime_checkable
class ArchiveStore(Protocol):
    backend_name: str  # "local" | "gdrive"

    async def put_input(self, sha256: str, ext: str, data: bytes, mime: str) -> BlobMeta: ...

    async def get_input(self, sha256: str, ext: str, remote_id: str = "") -> bytes | None: ...

    async def put_output_stream(
        self,
        profile_id: str,
        yyyy_mm: str,
        job_id: str,
        filename: str,
        stream: AsyncIterator[bytes],
        total_size: int | None,
        mime: str,
    ) -> BlobMeta: ...

    async def put_job_manifest(
        self, profile_id: str, yyyy_mm: str, job_id: str, manifest: dict
    ) -> str: ...

    async def open_output_stream(
        self, storage_key: str, remote_id: str, range_header: str | None = None
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]: ...

    async def delete_blob(self, storage_key: str, remote_id: str = "") -> bool: ...

    async def backup_db(self, db_snapshot_path: Path, meta: dict) -> bool: ...

    async def restore_db_if_missing(self, target_db_path: Path) -> bool: ...

    async def status_info(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class LocalArchiveStore:
    """Local filesystem implementation of ArchiveStore."""

    backend_name: str = "local"

    def __init__(self, archive_dir: str | Path, archive_max_gb: float = 20.0):
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.archive_max_gb = float(archive_max_gb)
        self.last_backup_at: float | None = None
        self.last_error: str = ""

    def _resolve_key(self, storage_key: str) -> Path:
        root = self.archive_dir.resolve()
        rel = Path(storage_key.lstrip("/"))
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as e:
            raise ValueError(f"Unsafe storage_key outside archive_dir: {storage_key!r}") from e
        return target

    @staticmethod
    def _input_key(sha256: str, ext: str, mime: str = "") -> str:
        safe_sha = _safe_segment(sha256, fallback="00")
        norm_ext = _norm_ext(ext, mime)
        prefix = safe_sha[:2] if len(safe_sha) >= 2 else safe_sha.ljust(2, "0")
        return f"inputs/{prefix}/{safe_sha}{norm_ext}"

    @staticmethod
    def _output_key(profile_id: str, yyyy_mm: str, job_id: str, filename: str) -> str:
        p = _safe_segment(profile_id, "default")
        ym = _safe_segment(yyyy_mm, "1970-01")
        j = _safe_segment(job_id, "job")
        fn = _safe_segment(filename, "output.bin")
        return f"archive/{p}/{ym}/{j}/{fn}"

    async def put_input(self, sha256: str, ext: str, data: bytes, mime: str) -> BlobMeta:
        digest = sha256 or hashlib.sha256(data).hexdigest()
        key = self._input_key(digest, ext, mime)
        dest = self._resolve_key(key)
        if not dest.is_file() or dest.stat().st_size != len(data):
            await asyncio.to_thread(atomic_write_bytes, dest, data)
        return BlobMeta(
            storage_backend="local",
            storage_key=key,
            remote_id="",
            size_bytes=len(data),
            sha256=digest,
            mime=mime or guess_mime(key),
        )

    async def get_input(self, sha256: str, ext: str, remote_id: str = "") -> bytes | None:
        del remote_id
        key = self._input_key(sha256, ext)
        path = self._resolve_key(key)
        if not path.is_file():
            return None
        return await asyncio.to_thread(path.read_bytes)

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
        del total_size
        key = self._output_key(profile_id, yyyy_mm, job_id, filename)
        dest = self._resolve_key(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(dest.parent))
        tmp_path = Path(tmp_name)
        hasher = hashlib.sha256()
        size_bytes = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in stream:
                    if not chunk:
                        continue
                    hasher.update(chunk)
                    size_bytes += len(chunk)
                    await asyncio.to_thread(f.write, chunk)
                await asyncio.to_thread(f.flush)
            os.replace(tmp_path, dest)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return BlobMeta(
            storage_backend="local",
            storage_key=key,
            remote_id="",
            size_bytes=size_bytes,
            sha256=hasher.hexdigest(),
            mime=mime or guess_mime(filename),
        )

    async def put_job_manifest(
        self, profile_id: str, yyyy_mm: str, job_id: str, manifest: dict
    ) -> str:
        key = self._output_key(profile_id, yyyy_mm, job_id, "job.json")
        dest = self._resolve_key(key)
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        await asyncio.to_thread(atomic_write_bytes, dest, payload)
        return key

    async def open_output_stream(
        self, storage_key: str, remote_id: str, range_header: str | None = None
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        del remote_id
        path = self._resolve_key(storage_key)
        if not path.is_file():
            raise FileNotFoundError(storage_key)
        total_size = path.stat().st_size
        mime = guess_mime(path.name)
        parsed = parse_range_header(range_header, total_size)

        if parsed == "invalid":
            async def _empty() -> AsyncIterator[bytes]:
                if False:
                    yield b""

            return (
                416,
                {
                    "Content-Range": f"bytes */{total_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": "0",
                    "Content-Type": mime,
                },
                _empty(),
            )

        if parsed is None:
            status = 200
            start = 0
            end = max(0, total_size - 1)
            length = total_size
            headers = {
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
                "Content-Type": mime,
            }
        else:
            status = 206
            start, end = parsed
            length = end - start + 1
            headers = {
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
                "Content-Type": mime,
            }

        async def _iter_file() -> AsyncIterator[bytes]:
            if length <= 0:
                return
            chunk_size = 65536
            remaining = length
            with path.open("rb") as f:
                f.seek(start)
                while remaining > 0:
                    to_read = min(chunk_size, remaining)
                    data = await asyncio.to_thread(f.read, to_read)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return status, headers, _iter_file()

    async def delete_blob(self, storage_key: str, remote_id: str = "") -> bool:
        del remote_id
        path = self._resolve_key(storage_key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    async def backup_db(self, db_snapshot_path: Path, meta: dict) -> bool:
        try:
            src = Path(db_snapshot_path)
            if not src.is_file():
                raise FileNotFoundError(f"Snapshot missing: {src}")
            db_dest = self._resolve_key("db/gate.db")
            meta_dest = self._resolve_key("db/gate.db.meta.json")
            data = await asyncio.to_thread(src.read_bytes)
            await asyncio.to_thread(atomic_write_bytes, db_dest, data)
            meta_bytes = json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8")
            await asyncio.to_thread(atomic_write_bytes, meta_dest, meta_bytes)
            self.last_backup_at = time.time()
            self.last_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.warning("Local DB backup failed: %s", e)
            return False

    async def restore_db_if_missing(self, target_db_path: Path) -> bool:
        target = Path(target_db_path)
        if target.exists():
            return False
        src = self._resolve_key("db/gate.db")
        if not src.is_file():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".restore-", suffix=".db.tmp", dir=str(target.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            await asyncio.to_thread(shutil.copy2, src, tmp_path)
            if not await asyncio.to_thread(verify_sqlite_integrity, tmp_path):
                tmp_path.unlink(missing_ok=True)
                return False
            os.replace(tmp_path, target)
            return True
        except Exception:  # noqa: BLE001
            tmp_path.unlink(missing_ok=True)
            return False

    def prune_unpinned(self, pinned_keys: set[str]) -> list[str]:
        """LRU-prune unpinned blobs under archive_dir if total exceeds archive_max_gb."""
        if self.archive_max_gb <= 0:
            return []
        max_bytes = int(self.archive_max_gb * (1024 ** 3))
        root = self.archive_dir.resolve()
        entries: list[tuple[float, int, str, Path]] = []
        total_bytes = 0
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith(".tmp-"):
                continue
            rel = p.relative_to(root).as_posix()
            if rel.startswith("db/"):
                continue
            st = p.stat()
            total_bytes += st.st_size
            if rel not in pinned_keys:
                entries.append((st.st_mtime, st.st_size, rel, p))
        if total_bytes <= max_bytes:
            return []
        entries.sort(key=lambda item: item[0])  # oldest mtime first
        deleted: list[str] = []
        for _mtime, sz, rel, p in entries:
            if total_bytes <= max_bytes:
                break
            try:
                p.unlink()
                total_bytes -= sz
                deleted.append(rel)
            except OSError:
                continue
        return deleted

    async def status_info(self) -> dict[str, Any]:
        def _calc_used() -> int:
            total = 0
            if self.archive_dir.is_dir():
                for p in self.archive_dir.rglob("*"):
                    if p.is_file():
                        total += p.stat().st_size
            return total

        used_bytes = await asyncio.to_thread(_calc_used)
        return {
            "backend": "local",
            "archive_dir": str(self.archive_dir),
            "archive_max_gb": self.archive_max_gb,
            "used_bytes": used_bytes,
            "last_backup_at": self.last_backup_at,
            "last_error": self.last_error,
        }

    async def close(self) -> None:
        return None


def save_thumbnail(
    thumbs_dir: Path, profile_id: str, job_id: str, idx: int, data: bytes, mime: str
) -> str:
    """Create a <=384px thumbnail on disk and return its relative path, or '' on fallback/skip."""
    if not data:
        return ""
    safe_profile = _safe_segment(profile_id, "default")
    safe_job = _safe_segment(job_id, "job")
    base_mime = (mime or "").split(";", 1)[0].strip().lower()
    out_dir = Path(thumbs_dir) / safe_profile

    if base_mime.startswith("image/"):
        # 1) Try Pillow WebP resize <= 384px
        try:
            from io import BytesIO
            from PIL import Image  # type: ignore[import-not-found]

            out_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(BytesIO(data)) as img:
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
                img.thumbnail((384, 384))
                rel = f"{safe_profile}/{safe_job}_{idx}.webp"
                buf = BytesIO()
                img.save(buf, format="WEBP", quality=82)
                atomic_write_bytes(Path(thumbs_dir) / rel, buf.getvalue())
                return rel
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            log.debug("Pillow thumbnail generation skipped: %s", e)

        # 2) Fallback: small raw image <= 512 KiB
        if len(data) <= 512 * 1024:
            ext = _MIME_TO_EXT.get(base_mime, ".png")
            rel = f"{safe_profile}/{safe_job}_{idx}{ext}"
            try:
                atomic_write_bytes(Path(thumbs_dir) / rel, data)
                return rel
            except Exception as e:  # noqa: BLE001
                log.debug("Raw thumbnail write failed: %s", e)
                return ""
        return ""

    if base_mime.startswith("video/"):
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return ""
        out_dir.mkdir(parents=True, exist_ok=True)
        in_ext = _MIME_TO_EXT.get(base_mime, ".mp4")
        tmp_in_path: Path | None = None
        tmp_out_path: Path | None = None
        try:
            fd_in, tmp_in_name = tempfile.mkstemp(prefix=".vid-", suffix=in_ext)
            tmp_in_path = Path(tmp_in_name)
            with os.fdopen(fd_in, "wb") as f:
                f.write(data)
            for ext in (".webp", ".jpg"):
                fd_out, tmp_out_name = tempfile.mkstemp(prefix=".thumb-", suffix=ext, dir=str(out_dir))
                os.close(fd_out)
                tmp_out_path = Path(tmp_out_name)
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(tmp_in_path),
                    "-vf",
                    "scale='min(384,iw)':'min(384,ih)':force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    str(tmp_out_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, timeout=15, check=False)  # noqa: S603
                if proc.returncode == 0 and tmp_out_path.is_file() and tmp_out_path.stat().st_size > 0:
                    rel = f"{safe_profile}/{safe_job}_{idx}{ext}"
                    os.replace(tmp_out_path, Path(thumbs_dir) / rel)
                    tmp_out_path = None
                    return rel
                tmp_out_path.unlink(missing_ok=True)
                tmp_out_path = None
        except Exception as e:  # noqa: BLE001
            log.debug("ffmpeg video thumbnail failed: %s", e)
        finally:
            if tmp_in_path is not None:
                tmp_in_path.unlink(missing_ok=True)
            if tmp_out_path is not None:
                tmp_out_path.unlink(missing_ok=True)
        return ""

    return ""


def build_store(settings: Any) -> ArchiveStore:
    """Instantiate GoogleDriveStore if gdrive_oauth_file is readable JSON, else LocalArchiveStore."""
    state_dir = Path(getattr(settings, "state_dir", "./data/state") or "./data/state")
    archive_dir = getattr(settings, "archive_dir", "") or str(state_dir.parent / "archive")
    archive_max_gb = float(getattr(settings, "archive_max_gb", 20.0))
    oauth_file = (getattr(settings, "gdrive_oauth_file", "") or "").strip()

    if oauth_file:
        oauth_path = Path(oauth_file)
        if oauth_path.is_file():
            try:
                parsed = json.loads(oauth_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and parsed.get("client_id") and parsed.get("refresh_token"):
                    from .gdrive import GoogleDriveStore

                    return GoogleDriveStore(
                        oauth_file=oauth_path,
                        root_folder_id=(getattr(settings, "gdrive_root_folder_id", "") or "").strip(),
                        cache_dir=archive_dir,
                        archive_max_gb=archive_max_gb,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("Failed to parse GDRIVE_OAUTH_FILE %s (%s); falling back to local store", oauth_path, e)

    return LocalArchiveStore(archive_dir=archive_dir, archive_max_gb=archive_max_gb)
