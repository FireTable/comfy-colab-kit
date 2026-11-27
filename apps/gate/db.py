"""SQLite database layer for Comfy Colab Kit gate.

Manages agent tokens, RFC 8628 device codes, and dynamic owners.
Database is stored in STATE_DIR / "gate.db" with WAL mode for concurrency.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from aiohttp import web

DB_KEY = web.AppKey("db", object)

ACTIVE_JOB_STATUSES = ("queued", "running", "archiving", "interrupted")
TERMINAL_JOB_STATUSES = ("completed", "failed", "canceled")
ALL_JOB_STATUSES = ACTIVE_JOB_STATUSES + TERMINAL_JOB_STATUSES


def _random_user_code() -> str:
    # 8-char base32-like alphabet (no confusing chars: 0/O, 1/I/L)
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(random.choice(chars) for _ in range(4))
    part2 = "".join(random.choice(chars) for _ in range(4))
    return f"{part1}-{part2}"


class GateDB:
    def __init__(self, db_path: str | Path, defer_init: bool = False):
        self.path = Path(db_path)
        self._initialized = False
        if not defer_init:
            self.ensure_initialized()

    def ensure_initialized(self) -> None:
        if not self._initialized:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            self._initialized = True

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        self.ensure_initialized()
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            with conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS agent_tokens (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        email TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        last_used_at REAL,
                        revoked INTEGER NOT NULL DEFAULT 0,
                        token_plain TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_tokens_hash ON agent_tokens(token_hash);
                    CREATE INDEX IF NOT EXISTS idx_tokens_email ON agent_tokens(email);

                    CREATE TABLE IF NOT EXISTS device_codes (
                        device_code TEXT PRIMARY KEY,
                        user_code TEXT NOT NULL UNIQUE,
                        client_name TEXT,
                        email TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        token_id TEXT,
                        token_plain TEXT,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_device_user_code ON device_codes(user_code);

                    CREATE TABLE IF NOT EXISTS dynamic_owners (
                        email TEXT PRIMARY KEY,
                        added_by TEXT,
                        created_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS assets (
                        id TEXT PRIMARY KEY,
                        sha256 TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        mime TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        storage_backend TEXT NOT NULL,
                        storage_key TEXT NOT NULL,
                        remote_id TEXT NOT NULL DEFAULT '',
                        thumb_relpath TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_assets_sha_kind ON assets(sha256, kind);
                    CREATE INDEX IF NOT EXISTS idx_assets_storage_key ON assets(storage_key);
                    CREATE INDEX IF NOT EXISTS idx_assets_filename ON assets(filename);

                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        client_prompt_id TEXT NOT NULL UNIQUE,
                        comfy_prompt_id TEXT NOT NULL DEFAULT '',
                        profile_id TEXT NOT NULL,
                        workflow_id TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        submitted_by TEXT NOT NULL DEFAULT '',
                        accelerator TEXT NOT NULL DEFAULT '',
                        prompt_json TEXT NOT NULL,
                        extra_data_json TEXT NOT NULL DEFAULT '{}',
                        prompt_text TEXT NOT NULL DEFAULT '',
                        seed INTEGER,
                        error_message TEXT NOT NULL DEFAULT '',
                        error_node TEXT NOT NULL DEFAULT '',
                        retries INTEGER NOT NULL DEFAULT 0,
                        max_retries INTEGER NOT NULL DEFAULT 2,
                        pinned INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        started_at REAL,
                        finished_at REAL,
                        elapsed_s REAL,
                        est_cu REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_jobs_profile_created ON jobs(profile_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at ASC);
                    CREATE INDEX IF NOT EXISTS idx_jobs_comfy_prompt_id ON jobs(comfy_prompt_id);

                    CREATE TABLE IF NOT EXISTS job_assets (
                        job_id TEXT NOT NULL,
                        asset_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        node_id TEXT NOT NULL DEFAULT '',
                        field_name TEXT NOT NULL DEFAULT '',
                        idx INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (job_id, asset_id, role, idx)
                    );
                    CREATE INDEX IF NOT EXISTS idx_job_assets_asset_id ON job_assets(asset_id);
                """)
                try:
                    conn.execute("ALTER TABLE agent_tokens ADD COLUMN token_plain TEXT;")
                except Exception:
                    pass
        finally:
            conn.close()

    def create_backup_snapshot(self, dest_path: str | Path) -> Path:
        """Create a consistent SQLite online backup at dest_path atomically."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.name}.{secrets.token_hex(4)}.tmp")
        self.ensure_initialized()
        src = sqlite3.connect(str(self.path), timeout=15.0)
        dst = sqlite3.connect(str(tmp), timeout=15.0)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()
        os.replace(tmp, dest)
        return dest

    # ── Device Code Authorization (RFC 8628) ──────────────────────────────────
    def create_device_code(self, client_name: str = "", expires_in_sec: int = 600) -> dict[str, Any]:
        device_code = f"dev_{secrets.token_hex(16)}"
        user_code = _random_user_code()
        now = time.time()
        expires_at = now + expires_in_sec

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO device_codes (device_code, user_code, client_name, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (device_code, user_code, client_name or "AI Agent", now, expires_at)
            )

        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": expires_in_sec,
            "interval": 3,
        }

    def get_device_code_info(self, user_code: str) -> dict[str, Any] | None:
        code = user_code.strip().upper()
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM device_codes WHERE user_code = ?", (code,)).fetchone()
            if not row:
                return None
            now = time.time()
            if row["status"] == "pending" and now > row["expires_at"]:
                conn.execute("UPDATE device_codes SET status = 'expired' WHERE user_code = ?", (code,))
                return {**dict(row), "status": "expired"}
            return dict(row)

    def approve_device_code(self, user_code: str, email: str, token_name: str = "") -> dict[str, Any]:
        code = user_code.strip().upper()
        now = time.time()
        raw_token = f"sk_agent_{secrets.token_hex(24)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_id = f"tok_{secrets.token_hex(6)}"

        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM device_codes WHERE user_code = ?", (code,)).fetchone()
            if not row:
                raise ValueError("invalid_user_code")
            if row["status"] != "pending":
                raise ValueError(f"code_already_{row['status']}")
            if now > row["expires_at"]:
                conn.execute("UPDATE device_codes SET status = 'expired' WHERE user_code = ?", (code,))
                raise ValueError("code_expired")

            name = token_name or row["client_name"] or "AI Agent"
            conn.execute(
                """INSERT INTO agent_tokens (id, name, token_hash, email, created_at, revoked, token_plain)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (token_id, name, token_hash, email.lower(), now, raw_token)
            )
            conn.execute(
                """UPDATE device_codes
                   SET status = 'approved', email = ?, token_id = ?, token_plain = ?
                   WHERE user_code = ?""",
                (email.lower(), token_id, raw_token, code)
            )

        return {
            "token_id": token_id,
            "name": name,
            "email": email.lower(),
            "token": raw_token,
        }

    def deny_device_code(self, user_code: str) -> bool:
        code = user_code.strip().upper()
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE device_codes SET status = 'denied' WHERE user_code = ? AND status = 'pending'",
                (code,)
            )
            return cur.rowcount > 0

    def poll_device_token(self, device_code: str) -> dict[str, Any]:
        now = time.time()
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM device_codes WHERE device_code = ?", (device_code,)).fetchone()
            if not row:
                return {"status": "not_found"}
            if row["status"] == "pending":
                if now > row["expires_at"]:
                    conn.execute("UPDATE device_codes SET status = 'expired' WHERE device_code = ?", (device_code,))
                    return {"status": "expired"}
                return {"status": "pending"}
            if row["status"] == "approved":
                token = row["token_plain"]
                if token:
                    # Clear token_plain immediately after first retrieval for security
                    conn.execute("UPDATE device_codes SET token_plain = NULL WHERE device_code = ?", (device_code,))
                return {
                    "status": "approved",
                    "access_token": token,
                    "token_id": row["token_id"],
                    "email": row["email"],
                }
            return {"status": row["status"]}

    # ── Token Management ──────────────────────────────────────────────────────
    def list_tokens(self, email: str = "", is_owner: bool = False) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            if is_owner and not email:
                rows = conn.execute("SELECT * FROM agent_tokens WHERE revoked = 0 ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_tokens WHERE email = ? AND revoked = 0 ORDER BY created_at DESC",
                    (email.lower(),)
                ).fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "email": r["email"],
                    "created_at": r["created_at"],
                    "last_used_at": r["last_used_at"],
                    "revoked": bool(r["revoked"]),
                    "token": r["token_plain"] if "token_plain" in r.keys() and r["token_plain"] else "",
                }
                for r in rows
            ]

    def create_direct_token(self, email: str, name: str, force_new: bool = False) -> dict[str, Any]:
        norm_email = email.lower()
        now = time.time()
        with self._get_conn() as conn:
            if not force_new:
                existing = conn.execute(
                    """SELECT * FROM agent_tokens
                       WHERE email = ? AND revoked = 0 AND last_used_at IS NULL AND token_plain IS NOT NULL AND token_plain != ''
                       ORDER BY created_at DESC LIMIT 1""",
                    (norm_email,)
                ).fetchone()
                if existing:
                    return {
                        "id": existing["id"],
                        "token_id": existing["id"],
                        "name": existing["name"],
                        "email": existing["email"],
                        "token": existing["token_plain"],
                        "created_at": existing["created_at"],
                        "reused": True,
                    }

            raw_token = f"sk_agent_{secrets.token_hex(24)}"
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            token_id = f"tok_{secrets.token_hex(6)}"

            conn.execute(
                """INSERT INTO agent_tokens (id, name, token_hash, email, created_at, revoked, token_plain)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (token_id, name or "AI Agent", token_hash, norm_email, now, raw_token)
            )

        return {
            "id": token_id,
            "token_id": token_id,
            "name": name,
            "email": norm_email,
            "token": raw_token,
            "created_at": now,
            "reused": False,
        }

    def delete_token(self, token_id: str, email: str, is_owner: bool = False) -> bool:
        with self._get_conn() as conn:
            if is_owner:
                cur = conn.execute("DELETE FROM agent_tokens WHERE id = ?", (token_id,))
            else:
                cur = conn.execute(
                    "DELETE FROM agent_tokens WHERE id = ? AND email = ?",
                    (token_id, email.lower())
                )
            return cur.rowcount > 0

    def revoke_token(self, token_id: str, email: str, is_owner: bool = False) -> bool:
        return self.delete_token(token_id, email, is_owner)

    def verify_token(self, raw_token: str) -> dict[str, Any] | None:
        if not raw_token or not raw_token.startswith("sk_agent_"):
            return None
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = time.time()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_tokens WHERE token_hash = ? AND revoked = 0",
                (token_hash,)
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE agent_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
            return {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "created_at": row["created_at"],
                "last_used_at": now,
            }

    # ── Dynamic Owners Management ─────────────────────────────────────────────
    def list_owners(self) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT email FROM dynamic_owners ORDER BY email ASC").fetchall()
            return [r["email"] for r in rows]

    def add_owner(self, email: str, added_by: str = "") -> bool:
        norm = email.strip().lower()
        if not norm or "@" not in norm:
            return False
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO dynamic_owners (email, added_by, created_at) VALUES (?, ?, ?)",
                (norm, added_by.lower(), time.time())
            )
            return True

    def remove_owner(self, email: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM dynamic_owners WHERE email = ?", (email.strip().lower(),))
            return cur.rowcount > 0

    # ── Assets & Durable Job Queue ────────────────────────────────────────────
    @staticmethod
    def _asset_dict(row: sqlite3.Row, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        aid = row["id"]
        thumb_rel = row["thumb_relpath"] or ""
        d = {
            "id": aid,
            "asset_id": aid,
            "sha256": row["sha256"],
            "kind": row["kind"],
            "filename": row["filename"],
            "mime": row["mime"],
            "size_bytes": int(row["size_bytes"]),
            "storage_backend": row["storage_backend"],
            "storage_key": row["storage_key"],
            "remote_id": row["remote_id"] or "",
            "thumb_relpath": thumb_rel,
            "created_at": float(row["created_at"]),
            "url": f"/_gate/archive/assets/{aid}",
            "thumb_url": f"/_gate/archive/thumbs/{aid}" if thumb_rel else "",
        }
        if extra:
            d.update(extra)
        return d

    def _attach_job_assets(self, conn: sqlite3.Connection, jobs: list[dict[str, Any]]) -> None:
        if not jobs:
            return
        by_id = {j["id"]: j for j in jobs}
        for j in jobs:
            j["inputs"] = []
            j["outputs"] = []
        placeholders = ",".join("?" for _ in jobs)
        rows = conn.execute(
            f"""SELECT ja.job_id, ja.role, ja.node_id, ja.field_name, ja.idx, a.*
                FROM job_assets ja
                JOIN assets a ON a.id = ja.asset_id
                WHERE ja.job_id IN ({placeholders})
                ORDER BY ja.idx ASC, a.created_at ASC""",
            tuple(by_id.keys()),
        ).fetchall()
        for r in rows:
            j = by_id.get(r["job_id"])
            if not j:
                continue
            item = self._asset_dict(r, {
                "role": r["role"],
                "node_id": r["node_id"] or "",
                "field_name": r["field_name"] or "",
                "idx": int(r["idx"]),
            })
            if r["role"] == "input":
                j["inputs"].append(item)
            else:
                j["outputs"].append(item)

    @staticmethod
    def _job_row_to_dict(row: sqlite3.Row, include_prompt: bool = True) -> dict[str, Any]:
        try:
            extra_data = json.loads(row["extra_data_json"] or "{}")
        except ValueError:
            extra_data = {}
        d: dict[str, Any] = {
            "id": row["id"],
            "client_prompt_id": row["client_prompt_id"],
            "comfy_prompt_id": row["comfy_prompt_id"] or "",
            "profile_id": row["profile_id"],
            "workflow_id": row["workflow_id"] or "",
            "status": row["status"],
            "submitted_by": row["submitted_by"] or "",
            "accelerator": row["accelerator"] or "",
            "prompt_text": row["prompt_text"] or "",
            "seed": row["seed"],
            "error_message": row["error_message"] or "",
            "error_node": row["error_node"] or "",
            "retries": int(row["retries"]),
            "max_retries": int(row["max_retries"]),
            "pinned": bool(row["pinned"]),
            "created_at": float(row["created_at"]),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "elapsed_s": row["elapsed_s"],
            "est_cu": row["est_cu"],
            "extra_data": extra_data,
        }
        if include_prompt:
            try:
                d["prompt"] = json.loads(row["prompt_json"] or "{}")
            except ValueError:
                d["prompt"] = {}
        return d

    def upsert_input_asset(
        self,
        sha256: str,
        filename: str,
        mime: str,
        size_bytes: int,
        storage_backend: str,
        storage_key: str,
        remote_id: str = "",
        thumb_relpath: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE sha256 = ? AND kind = 'input' AND filename = ? LIMIT 1",
                (sha256, filename),
            ).fetchone()
            if row:
                new_remote = remote_id or row["remote_id"] or ""
                new_thumb = thumb_relpath or row["thumb_relpath"] or ""
                conn.execute(
                    """UPDATE assets
                       SET mime = ?, size_bytes = ?, storage_backend = ?, storage_key = ?,
                           remote_id = ?, thumb_relpath = ?
                       WHERE id = ?""",
                    (mime, int(size_bytes), storage_backend, storage_key, new_remote, new_thumb, row["id"]),
                )
                updated = conn.execute("SELECT * FROM assets WHERE id = ?", (row["id"],)).fetchone()
                return self._asset_dict(updated)

            aid = f"ast_{secrets.token_hex(8)}"
            conn.execute(
                """INSERT INTO assets
                   (id, sha256, kind, filename, mime, size_bytes, storage_backend, storage_key, remote_id, thumb_relpath, created_at)
                   VALUES (?, ?, 'input', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (aid, sha256, filename, mime, int(size_bytes), storage_backend, storage_key, remote_id or "", thumb_relpath or "", now),
            )
            inserted = conn.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
            return self._asset_dict(inserted)

    def find_input_asset_by_filename_or_sha(self, filename: str = "", sha256: str = "") -> dict[str, Any] | None:
        with self._get_conn() as conn:
            if filename:
                row = conn.execute(
                    "SELECT * FROM assets WHERE kind = 'input' AND filename = ? ORDER BY created_at DESC LIMIT 1",
                    (filename,),
                ).fetchone()
                if not row and "/" in filename:
                    base = Path(filename).name
                    row = conn.execute(
                        "SELECT * FROM assets WHERE kind = 'input' AND filename = ? ORDER BY created_at DESC LIMIT 1",
                        (base,),
                    ).fetchone()
                if row:
                    return self._asset_dict(row)
            if sha256:
                row = conn.execute(
                    "SELECT * FROM assets WHERE kind = 'input' AND sha256 = ? ORDER BY created_at DESC LIMIT 1",
                    (sha256,),
                ).fetchone()
                if row:
                    return self._asset_dict(row)
            return None

    def add_output_asset(
        self,
        sha256: str,
        filename: str,
        mime: str,
        size_bytes: int,
        storage_backend: str,
        storage_key: str,
        remote_id: str = "",
        thumb_relpath: str = "",
    ) -> dict[str, Any]:
        aid = f"ast_{secrets.token_hex(8)}"
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO assets
                   (id, sha256, kind, filename, mime, size_bytes, storage_backend, storage_key, remote_id, thumb_relpath, created_at)
                   VALUES (?, ?, 'output', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (aid, sha256, filename, mime, int(size_bytes), storage_backend, storage_key, remote_id or "", thumb_relpath or "", now),
            )
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
            return self._asset_dict(row)

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            return self._asset_dict(row) if row else None

    def create_job(
        self,
        client_prompt_id: str,
        profile_id: str,
        prompt_json: dict | str,
        *,
        job_id: str = "",
        workflow_id: str = "",
        status: str = "queued",
        submitted_by: str = "",
        accelerator: str = "",
        extra_data_json: dict | str = "{}",
        prompt_text: str = "",
        seed: int | None = None,
        max_retries: int = 2,
        pinned: int | bool = 0,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        jid = job_id or f"job_{secrets.token_hex(8)}"
        now = created_at if created_at is not None else time.time()
        p_str = json.dumps(prompt_json) if not isinstance(prompt_json, str) else prompt_json
        e_str = json.dumps(extra_data_json) if not isinstance(extra_data_json, str) else extra_data_json
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, client_prompt_id, comfy_prompt_id, profile_id, workflow_id, status, submitted_by,
                    accelerator, prompt_json, extra_data_json, prompt_text, seed, retries, max_retries, pinned, created_at)
                   VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (jid, client_prompt_id, profile_id, workflow_id, status, submitted_by,
                 accelerator, p_str, e_str, prompt_text, seed, int(max_retries), 1 if pinned else 0, now),
            )
        job = self.get_job(jid, include_prompt=True)
        assert job is not None
        return job

    def link_job_asset(
        self,
        job_id: str,
        asset_id: str,
        role: str,
        node_id: str = "",
        field_name: str = "",
        idx: int = 0,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO job_assets (job_id, asset_id, role, node_id, field_name, idx)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, asset_id, role, node_id, field_name, int(idx)),
            )

    def get_job(self, job_id_or_client_prompt_id: str, include_prompt: bool = True) -> dict[str, Any] | None:
        key = (job_id_or_client_prompt_id or "").strip()
        if not key:
            return None
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? OR client_prompt_id = ? LIMIT 1",
                (key, key),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE comfy_prompt_id = ? LIMIT 1",
                    (key,),
                ).fetchone()
            if not row:
                return None
            job = self._job_row_to_dict(row, include_prompt=include_prompt)
            self._attach_job_assets(conn, [job])
            return job

    def _build_job_filters(
        self,
        profile: str | None = None,
        status: str | None = None,
        q: str | None = None,
        pinned: bool | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if profile and profile.lower() != "all":
            clauses.append("profile_id = ?")
            args.append(profile)
        if status and status.lower() != "all":
            statuses = [s.strip().lower() for s in status.split(",") if s.strip()]
            if len(statuses) == 1:
                clauses.append("status = ?")
                args.append(statuses[0])
            elif statuses:
                ph = ",".join("?" for _ in statuses)
                clauses.append(f"status IN ({ph})")
                args.extend(statuses)
        if pinned is True:
            clauses.append("pinned = 1")
        elif pinned is False:
            clauses.append("pinned = 0")
        if q and q.strip():
            like = f"%{q.strip().lower()}%"
            clauses.append("(LOWER(prompt_text) LIKE ? OR LOWER(id) LIKE ? OR LOWER(profile_id) LIKE ? OR LOWER(error_message) LIKE ?)")
            args.extend([like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, args

    def list_jobs(
        self,
        profile: str | None = None,
        status: str | None = None,
        q: str | None = None,
        pinned: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        include_prompt: bool = False,
    ) -> list[dict[str, Any]]:
        where, args = self._build_job_filters(profile=profile, status=status, q=q, pinned=pinned)
        lim = max(1, min(200, int(limit)))
        off = max(0, int(offset))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*args, lim, off),
            ).fetchall()
            jobs = [self._job_row_to_dict(r, include_prompt=include_prompt) for r in rows]
            self._attach_job_assets(conn, jobs)
            return jobs

    def count_jobs(
        self,
        profile: str | None = None,
        status: str | None = None,
        q: str | None = None,
        pinned: bool | None = None,
    ) -> int:
        where, args = self._build_job_filters(profile=profile, status=status, q=q, pinned=pinned)
        with self._get_conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS cnt FROM jobs {where}", tuple(args)).fetchone()
            return int(row["cnt"]) if row else 0

    _UNSET: Any = object()

    def update_job_status(
        self,
        job_id: str,
        status: str,
        *,
        comfy_prompt_id: Any = _UNSET,
        accelerator: Any = _UNSET,
        error_message: Any = _UNSET,
        error_node: Any = _UNSET,
        retries: Any = _UNSET,
        max_retries: Any = _UNSET,
        started_at: Any = _UNSET,
        finished_at: Any = _UNSET,
        elapsed_s: Any = _UNSET,
        est_cu: Any = _UNSET,
    ) -> dict[str, Any] | None:
        fields: list[str] = ["status = ?"]
        vals: list[Any] = [status]
        mapping = {
            "comfy_prompt_id": comfy_prompt_id,
            "accelerator": accelerator,
            "error_message": error_message,
            "error_node": error_node,
            "retries": retries,
            "max_retries": max_retries,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_s": elapsed_s,
            "est_cu": est_cu,
        }
        for col, val in mapping.items():
            if val is not self._UNSET:
                fields.append(f"{col} = ?")
                vals.append(val)
        vals.append(job_id)
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", tuple(vals))
            if cur.rowcount == 0:
                return None
        return self.get_job(job_id, include_prompt=True)

    def mark_all_running_interrupted(self, reason: str = "VM stopped or crashed") -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                """UPDATE jobs
                   SET status = 'interrupted', error_message = ?
                   WHERE status IN ('running', 'archiving')""",
                (reason,),
            )
            return cur.rowcount

    def next_dispatchable_job(self, profile_id: str, include_interrupted_retry: bool = True) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            if include_interrupted_retry:
                row = conn.execute(
                    """SELECT * FROM jobs
                       WHERE profile_id = ?
                         AND (status = 'queued' OR (status = 'interrupted' AND retries < max_retries))
                       ORDER BY created_at ASC LIMIT 1""",
                    (profile_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM jobs
                       WHERE profile_id = ? AND status = 'queued'
                       ORDER BY created_at ASC LIMIT 1""",
                    (profile_id,),
                ).fetchone()
            if not row:
                return None
            job = self._job_row_to_dict(row, include_prompt=True)
            self._attach_job_assets(conn, [job])
            return job

    def count_jobs_by_status(self, profile_id: str | None = None) -> dict[str, int]:
        counts = {s: 0 for s in ALL_JOB_STATUSES}
        total = 0
        with self._get_conn() as conn:
            if profile_id and profile_id.lower() != "all":
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS cnt FROM jobs WHERE profile_id = ? GROUP BY status",
                    (profile_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status").fetchall()
            for r in rows:
                st = r["status"]
                cnt = int(r["cnt"])
                counts[st] = cnt
                total += cnt
        counts["total"] = total
        return counts

    def profile_archive_stats(self) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT
                       j.profile_id AS profile_id,
                       COUNT(DISTINCT j.id) AS total_jobs,
                       SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN j.status = 'queued' THEN 1 ELSE 0 END) AS queued,
                       SUM(CASE WHEN j.status IN ('running', 'archiving') THEN 1 ELSE 0 END) AS running,
                       SUM(CASE WHEN j.status = 'interrupted' THEN 1 ELSE 0 END) AS interrupted,
                       SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN j.pinned = 1 THEN 1 ELSE 0 END) AS pinned,
                       MAX(j.created_at) AS latest_at
                   FROM jobs j
                   GROUP BY j.profile_id
                   ORDER BY latest_at DESC"""
            ).fetchall()
            out_rows = conn.execute(
                """SELECT j.profile_id AS profile_id, COUNT(a.id) AS output_count, COALESCE(SUM(a.size_bytes), 0) AS output_bytes
                   FROM jobs j
                   JOIN job_assets ja ON ja.job_id = j.id AND ja.role = 'output'
                   JOIN assets a ON a.id = ja.asset_id
                   GROUP BY j.profile_id"""
            ).fetchall()
            out_map = {r["profile_id"]: (int(r["output_count"]), int(r["output_bytes"])) for r in out_rows}
            result: list[dict[str, Any]] = []
            for r in rows:
                pid = r["profile_id"]
                oc, ob = out_map.get(pid, (0, 0))
                result.append({
                    "profile_id": pid,
                    "id": pid,
                    "total_jobs": int(r["total_jobs"]),
                    "count": int(r["total_jobs"]),
                    "completed": int(r["completed"] or 0),
                    "queued": int(r["queued"] or 0),
                    "running": int(r["running"] or 0),
                    "interrupted": int(r["interrupted"] or 0),
                    "failed": int(r["failed"] or 0),
                    "pinned": int(r["pinned"] or 0),
                    "output_count": oc,
                    "output_bytes": ob,
                    "latest_at": r["latest_at"],
                })
            return result

    def set_job_pinned(self, job_id: str, pinned: bool) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            cur = conn.execute("UPDATE jobs SET pinned = ? WHERE id = ?", (1 if pinned else 0, job_id))
            if cur.rowcount == 0:
                return None
        return self.get_job(job_id, include_prompt=True)

    def delete_job(self, job_id: str, delete_unshared_outputs: bool = False) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            exists = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not exists:
                return None
            unshared: list[dict[str, Any]] = []
            if delete_unshared_outputs:
                linked = conn.execute(
                    """SELECT a.* FROM job_assets ja
                       JOIN assets a ON a.id = ja.asset_id
                       WHERE ja.job_id = ? AND ja.role = 'output'""",
                    (job_id,),
                ).fetchall()
                conn.execute("DELETE FROM job_assets WHERE job_id = ?", (job_id,))
                for a in linked:
                    ref = conn.execute(
                        "SELECT 1 FROM job_assets WHERE asset_id = ? LIMIT 1",
                        (a["id"],),
                    ).fetchone()
                    if not ref:
                        unshared.append(self._asset_dict(a))
                        conn.execute("DELETE FROM assets WHERE id = ?", (a["id"],))
            else:
                conn.execute("DELETE FROM job_assets WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return {"deleted": True, "id": job_id, "unshared_assets": unshared}

    def list_pinned_storage_keys(self) -> set[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT a.storage_key
                   FROM jobs j
                   JOIN job_assets ja ON ja.job_id = j.id
                   JOIN assets a ON a.id = ja.asset_id
                   WHERE j.pinned = 1"""
            ).fetchall()
            return {r["storage_key"] for r in rows if r["storage_key"]}
