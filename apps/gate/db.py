"""SQLite database layer for Comfy Colab Kit gate.

Manages agent tokens, RFC 8628 device codes, and dynamic owners.
Database is stored in STATE_DIR / "gate.db" with WAL mode for concurrency.
"""
from __future__ import annotations

import hashlib
import os
import random
import secrets
import sqlite3
import string
import time
from pathlib import Path
from typing import Any
from aiohttp import web

DB_KEY = web.AppKey("db", object)


def _random_user_code() -> str:
    # 8-char base32-like alphabet (no confusing chars: 0/O, 1/I/L)
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part1 = "".join(random.choice(chars) for _ in range(4))
    part2 = "".join(random.choice(chars) for _ in range(4))
    return f"{part1}-{part2}"


class GateDB:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_tokens (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL,
                    revoked INTEGER NOT NULL DEFAULT 0
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
            """)

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
                """INSERT INTO agent_tokens (id, name, token_hash, email, created_at, revoked)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (token_id, name, token_hash, email.lower(), now)
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
                }
                for r in rows
            ]

    def create_direct_token(self, email: str, name: str) -> dict[str, Any]:
        raw_token = f"sk_agent_{secrets.token_hex(24)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_id = f"tok_{secrets.token_hex(6)}"
        now = time.time()

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO agent_tokens (id, name, token_hash, email, created_at, revoked)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (token_id, name or "AI Agent", token_hash, email.lower(), now)
            )

        return {
            "token_id": token_id,
            "name": name,
            "email": email.lower(),
            "token": raw_token,
            "created_at": now,
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
