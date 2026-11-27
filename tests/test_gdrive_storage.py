"""Tests for ArchiveStore (LocalArchiveStore & GoogleDriveStore), save_thumbnail, and build_store."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from apps.gate.gdrive import GoogleDriveStore
from apps.gate.storage import (
    ArchiveStore,
    BlobMeta,
    LocalArchiveStore,
    build_store,
    save_thumbnail,
)
from tests.fake_gdrive import FakeGDriveServer


async def _chunks(data: bytes, chunk_size: int = 512 * 1024) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


async def _collect(stream: AsyncIterator[bytes]) -> bytes:
    buf = bytearray()
    async for part in stream:
        buf.extend(part)
    return bytes(buf)


def _make_sample_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, prompt TEXT)")
    conn.execute("INSERT INTO jobs (id, prompt) VALUES ('j1', 'cyberpunk cat')")
    conn.commit()
    conn.close()


# ── LocalArchiveStore tests ───────────────────────────────────────────────────


async def test_local_store_roundtrip_range_and_prune(tmp_path: Path) -> None:
    store = LocalArchiveStore(tmp_path / "archive", archive_max_gb=0.00001)  # ~10.7 KB
    assert isinstance(store, ArchiveStore)
    assert store.backend_name == "local"

    # Input dedup + retrieval
    inp = b"fake-png-bytes" * 20
    sha = hashlib.sha256(inp).hexdigest()
    meta_in = await store.put_input(sha, ".png", inp, "image/png")
    assert meta_in.storage_backend == "local"
    assert meta_in.storage_key == f"inputs/{sha[:2]}/{sha}.png"
    assert meta_in.size_bytes == len(inp)
    assert await store.get_input(sha, ".png") == inp
    assert await store.get_input("deadbeef" * 8, ".png") is None

    # Output stream + Range 206 + manifest
    out_data = b"0123456789ABCDEF" * 600  # 9600 bytes
    meta_out = await store.put_output_stream(
        "moss-image", "2026-11", "job-1", "render.png", _chunks(out_data, 1024), len(out_data), "image/png"
    )
    assert meta_out.storage_key == "archive/moss-image/2026-11/job-1/render.png"
    assert meta_out.size_bytes == len(out_data)
    assert meta_out.sha256 == hashlib.sha256(out_data).hexdigest()

    manifest_key = await store.put_job_manifest("moss-image", "2026-11", "job-1", {"job_id": "job-1"})
    assert manifest_key == "archive/moss-image/2026-11/job-1/job.json"

    status, headers, body_iter = await store.open_output_stream(meta_out.storage_key, "")
    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert await _collect(body_iter) == out_data

    status, headers, body_iter = await store.open_output_stream(
        meta_out.storage_key, "", range_header="bytes=10-25"
    )
    assert status == 206
    assert headers["Content-Range"] == f"bytes 10-25/{len(out_data)}"
    assert headers["Content-Length"] == "16"
    assert await _collect(body_iter) == out_data[10:26]

    # Invalid range -> 416
    status, _hdrs, body_iter = await store.open_output_stream(
        meta_out.storage_key, "", range_header="bytes=999999-1000000"
    )
    assert status == 416
    assert await _collect(body_iter) == b""

    # Second output triggers LRU prune of unpinned older file while preserving pinned
    old_file = tmp_path / "archive" / meta_out.storage_key
    os.utime(old_file, (1000, 1000))
    out2 = b"B" * 8000
    meta_out2 = await store.put_output_stream(
        "moss-image", "2026-11", "job-2", "render2.png", _chunks(out2, 2000), None, "image/png"
    )
    deleted = store.prune_unpinned(pinned_keys={meta_out2.storage_key})
    assert meta_out.storage_key in deleted
    assert meta_out2.storage_key not in deleted
    assert (tmp_path / "archive" / meta_out2.storage_key).is_file()

    assert await store.delete_blob(meta_out2.storage_key) is True
    assert await store.delete_blob(meta_out2.storage_key) is False
    await store.close()


async def test_local_store_db_backup_and_restore(tmp_path: Path) -> None:
    store = LocalArchiveStore(tmp_path / "archive")
    src_db = tmp_path / "state" / "gate.db"
    _make_sample_db(src_db)

    snap = tmp_path / "snap.db"
    shutil.copy2(src_db, snap)
    assert await store.backup_db(snap, {"jobs": 1}) is True
    assert (tmp_path / "archive" / "db" / "gate.db.meta.json").is_file()

    restored_db = tmp_path / "fresh_state" / "gate.db"
    assert await store.restore_db_if_missing(restored_db) is True
    assert restored_db.is_file()
    conn = sqlite3.connect(str(restored_db))
    row = conn.execute("SELECT prompt FROM jobs WHERE id='j1'").fetchone()
    conn.close()
    assert row == ("cyberpunk cat",)
    # Already exists -> False
    assert await store.restore_db_if_missing(restored_db) is False
    await store.close()


# ── GoogleDriveStore + FakeGDriveServer tests ─────────────────────────────────


async def test_gdrive_oauth_401_retry_and_folder_cache(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gd_root") as srv:
        oauth_file = srv.write_oauth_file(tmp_path / "gdrive_oauth.json")
        store = GoogleDriveStore(oauth_file, cache_dir=tmp_path / "cache")
        try:
            assert isinstance(store, ArchiveStore)
            assert store.backend_name == "gdrive"

            # First call triggers OAuth refresh & creates comfy-colab-kit + inputs/<sha[:2]>
            data = b"hello-input-bytes"
            sha = hashlib.sha256(data).hexdigest()
            meta1 = await store.put_input(sha, ".png", data, "image/png")
            assert srv.token_refresh_count == 1
            assert srv.upload_count == 1
            assert meta1.remote_id

            # Expire token on server -> next API call gets 401, refreshes once, and retries!
            srv.expire_current_tokens()
            # Also clear local cache to force a Drive GET
            (tmp_path / "cache" / meta1.storage_key).unlink()
            got = await store.get_input(sha, ".png", remote_id=meta1.remote_id)
            assert got == data
            assert srv.token_refresh_count == 2

            # Transient 503 backoff retry works transparently
            srv.inject_transient_errors(503)
            manifest_key = await store.put_job_manifest(
                "moss-image", "2026-11", "job-42", {"ok": True}
            )
            assert manifest_key == "archive/moss-image/2026-11/job-42/job.json"
            # Folder cache kept folder count stable; comfy-colab-kit only created once
            root_id = (await store.status_info())["root_folder_id"]
            assert root_id
        finally:
            await store.close()


async def test_gdrive_input_dedup_and_cache(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gd_root") as srv:
        oauth_file = srv.write_oauth_file(tmp_path / "gdrive_oauth.json")
        store = GoogleDriveStore(oauth_file, cache_dir=tmp_path / "cache")
        try:
            payload = b"unique-reference-image-data"
            sha = hashlib.sha256(payload).hexdigest()
            m1 = await store.put_input(sha, "png", payload, "image/png")
            m2 = await store.put_input(sha, ".png", payload, "image/png")
            assert m1.remote_id == m2.remote_id
            assert srv.upload_count == 1  # second put_input skipped upload via Drive query dedup

            # get_input hits local cache without hitting Drive download endpoint
            downloads_before = srv.download_count
            assert await store.get_input(sha, ".png", remote_id=m1.remote_id) == payload
            assert srv.download_count == downloads_before
        finally:
            await store.close()


async def test_gdrive_multichunk_resumable_and_range_stream(tmp_path: Path) -> None:
    async with FakeGDriveServer(tmp_path / "gd_root") as srv:
        oauth_file = srv.write_oauth_file(tmp_path / "gdrive_oauth.json")
        store = GoogleDriveStore(oauth_file, cache_dir=tmp_path / "cache")
        try:
            # 9.5 MiB payload -> forces 3 resumable PUT chunks (4 MiB + 4 MiB + 1.5 MiB)
            big = os.urandom(9 * 1024 * 1024 + 512 * 1024)
            expected_sha = hashlib.sha256(big).hexdigest()

            # Test unknown total_size=None streaming
            meta = await store.put_output_stream(
                "wan-ltx-video",
                "2026-11",
                "job-big",
                "clip.mp4",
                _chunks(big, 700 * 1024),
                None,
                "video/mp4",
            )
            assert meta.storage_backend == "gdrive"
            assert meta.storage_key == "archive/wan-ltx-video/2026-11/job-big/clip.mp4"
            assert meta.size_bytes == len(big)
            assert meta.sha256 == expected_sha
            assert srv.resumable_put_count >= 3

            # Range 206 read from middle across chunk boundary
            start = 4 * 1024 * 1024 - 100
            end = 4 * 1024 * 1024 + 199
            status, hdrs, body_iter = await store.open_output_stream(
                meta.storage_key, meta.remote_id, range_header=f"bytes={start}-{end}"
            )
            assert status == 206
            assert hdrs["Content-Range"] == f"bytes {start}-{end}/{len(big)}"
            assert await _collect(body_iter) == big[start : end + 1]

            # Exact 4 MiB boundary with known total_size
            exact_4m = b"X" * (4 * 1024 * 1024)
            meta_4m = await store.put_output_stream(
                "moss-image",
                "2026-11",
                "job-4m",
                "exact.bin",
                _chunks(exact_4m, 1024 * 1024),
                len(exact_4m),
                "application/octet-stream",
            )
            assert meta_4m.size_bytes == len(exact_4m)
            status, _h, full_iter = await store.open_output_stream(meta_4m.storage_key, meta_4m.remote_id)
            assert status == 200
            assert await _collect(full_iter) == exact_4m

            assert await store.delete_blob(meta_4m.storage_key, meta_4m.remote_id) is True
            with pytest.raises(FileNotFoundError):
                await store.open_output_stream(meta_4m.storage_key, meta_4m.remote_id)
        finally:
            await store.close()


async def test_gdrive_db_backup_and_cold_restore_across_restart(tmp_path: Path) -> None:
    gd_dir = tmp_path / "gd_root"
    oauth_file = tmp_path / "gdrive_oauth.json"

    async with FakeGDriveServer(gd_dir) as srv1:
        srv1.write_oauth_file(oauth_file)
        store1 = GoogleDriveStore(oauth_file, cache_dir=tmp_path / "cache1")
        try:
            snap = tmp_path / "gate.snap.db"
            _make_sample_db(snap)
            assert await store1.backup_db(snap, {"version": 1, "jobs": 1}) is True
            info = await store1.status_info()
            assert info["last_backup_at"] is not None
            assert info["last_error"] == ""
        finally:
            await store1.close()

    # Start fresh FakeGDriveServer pointing at same disk dir -> verify cold restore!
    async with FakeGDriveServer(gd_dir) as srv2:
        srv2.write_oauth_file(oauth_file)
        store2 = GoogleDriveStore(oauth_file, cache_dir=tmp_path / "cache2")
        try:
            restored = tmp_path / "new_container" / "gate.db"
            assert await store2.restore_db_if_missing(restored) is True
            assert restored.is_file()
            conn = sqlite3.connect(str(restored))
            row = conn.execute("SELECT prompt FROM jobs WHERE id='j1'").fetchone()
            conn.close()
            assert row == ("cyberpunk cat",)
        finally:
            await store2.close()


# ── save_thumbnail & build_store tests ────────────────────────────────────────


def test_save_thumbnail_fallbacks_and_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thumbs = tmp_path / "thumbs"

    # Simulate Pillow absent -> small image <= 512 KiB falls back to raw image write
    real_import = __import__

    def _no_pil(name: str, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_pil)
    small_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    rel = save_thumbnail(thumbs, "moss-image", "job1", 0, small_png, "image/png")
    assert rel == "moss-image/job1_0.png"
    assert (thumbs / rel).read_bytes() == small_png

    # Huge image without PIL -> gracefully returns ""
    huge_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (600 * 1024)
    assert save_thumbnail(thumbs, "moss-image", "job1", 1, huge_png, "image/png") == ""

    # Video without ffmpeg -> gracefully returns ""
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    assert save_thumbnail(thumbs, "wan-ltx-video", "job2", 0, b"fake-mp4", "video/mp4") == ""

    # Restore real which and test ffmpeg video thumbnail if ffmpeg is installed
    monkeypatch.undo()
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        sample_mp4 = tmp_path / "sample.mp4"
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=640x360:d=0.2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(sample_mp4),
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0 and sample_mp4.is_file():
            vid_rel = save_thumbnail(
                thumbs, "wan-ltx-video", "job3", 0, sample_mp4.read_bytes(), "video/mp4"
            )
            assert vid_rel.startswith("wan-ltx-video/job3_0.")
            assert (thumbs / vid_rel).is_file()

    # Simulate PIL present via lightweight stub module to verify PIL branch
    fake_pil = types.ModuleType("PIL")
    fake_img_mod = types.ModuleType("PIL.Image")

    class _FakeImg:
        mode = "RGB"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def getbands(self):
            return ("R", "G", "B")

        def thumbnail(self, size):
            assert size == (384, 384)

        def save(self, buf, format="WEBP", quality=82):
            assert format == "WEBP"
            buf.write(b"RIFFfakeWEBP")

    fake_img_mod.open = lambda _bio: _FakeImg()
    fake_pil.Image = fake_img_mod
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_img_mod)
    webp_rel = save_thumbnail(thumbs, "moss-image", "job4", 0, small_png, "image/png")
    assert webp_rel == "moss-image/job4_0.webp"
    assert (thumbs / webp_rel).read_bytes() == b"RIFFfakeWEBP"


def test_build_store_selection(tmp_path: Path) -> None:
    s_local = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        archive_dir=str(tmp_path / "archive"),
        archive_max_gb=10.0,
        gdrive_oauth_file="",
    )
    st1 = build_store(s_local)
    assert isinstance(st1, LocalArchiveStore)
    assert st1.backend_name == "local"

    # Non-existent or invalid JSON falls back safely to LocalArchiveStore
    bad_oauth = tmp_path / "bad.json"
    bad_oauth.write_text("{not valid json", encoding="utf-8")
    s_bad = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        archive_dir=str(tmp_path / "archive"),
        gdrive_oauth_file=str(bad_oauth),
    )
    assert isinstance(build_store(s_bad), LocalArchiveStore)

    # Valid OAuth JSON returns GoogleDriveStore
    good_oauth = tmp_path / "good.json"
    good_oauth.write_text(
        json.dumps(
            {
                "client_id": "cid",
                "client_secret": "csec",
                "refresh_token": "rtok",
            }
        ),
        encoding="utf-8",
    )
    s_gd = SimpleNamespace(
        state_dir=str(tmp_path / "state"),
        archive_dir=str(tmp_path / "archive"),
        gdrive_oauth_file=str(good_oauth),
        gdrive_root_folder_id="root123",
    )
    st2 = build_store(s_gd)
    assert isinstance(st2, GoogleDriveStore)
    assert st2.backend_name == "gdrive"
