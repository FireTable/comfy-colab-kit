"""Thin async wrapper around the `colab` CLI (google-colab-cli).

The gate is the only component holding the Colab OAuth token (COLAB_TOKEN_DIR).
Secrets for the VM are sent on stdin of `colab exec` (never on argv) so they
do not show up in process listings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import AsyncIterator, Protocol

log = logging.getLogger("gate.colab")


class ColabError(RuntimeError):
    pass


class ColabRunner(Protocol):
    async def sessions(self) -> list[str]: ...
    async def new(self, name: str, accelerator: str) -> None: ...
    async def upload(self, name: str, src: str, dst: str) -> None: ...
    def exec_stream(self, name: str, code: str, timeout_s: int) -> AsyncIterator[str]: ...
    async def stop(self, name: str) -> None: ...
    async def ccu_info(self) -> float | None: ...
    async def touch_heartbeat(self, name: str) -> bool: ...


class ColabCLI:
    def __init__(self, binary: str = "colab", token_dir: str = ""):
        self.binary = binary
        self.env = dict(os.environ)
        if token_dir:
            # google-colab-cli keeps its token under ~/.config/colab-cli; point HOME-relative config there
            self.env["XDG_CONFIG_HOME"] = token_dir
        # the CLI is Python: without this its stdout is block-buffered on a pipe and `exec` output
        # (setup progress) would only arrive in 4-8 KB chunks or at the very end
        self.env["PYTHONUNBUFFERED"] = "1"

    async def _run(self, *args: str, stdin: str | None = None, timeout: float = 600) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.binary, *args, env=self.env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin else None), timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise ColabError(f"colab {args[0]} timed out") from e
        text = out.decode(errors="replace")
        if proc.returncode:
            raise ColabError(f"colab {args[0]} failed ({proc.returncode}): {text[-500:]}")
        return text

    async def sessions(self) -> list[str]:
        try:
            out = await self._run("sessions", timeout=60)
        except ColabError as e:
            msg = str(e).lower()
            if "no active session" in msg or "no session" in msg:
                return []
            raise
        return parse_sessions(out)

    async def new(self, name: str, accelerator: str) -> None:
        await self._run("new", "-s", name, "--gpu", accelerator, timeout=600)

    async def upload(self, name: str, src: str, dst: str) -> None:
        await self._run("upload", "-s", name, src, dst, timeout=600)

    async def exec_stream(self, name: str, code: str, timeout_s: int):
        proc = await asyncio.create_subprocess_exec(
            self.binary, "exec", "-s", name, "--timeout", str(timeout_s), env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdin and proc.stdout
        proc.stdin.write(code.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        async for raw in proc.stdout:
            yield raw.decode(errors="replace").rstrip("\n")
        rc = await proc.wait()
        if rc:
            raise ColabError(f"colab exec exited with {rc}")

    async def stop(self, name: str) -> None:
        try:
            await self._run("stop", "-s", name, timeout=180)
        except ColabError as e:
            msg = str(e).lower()
            if any(k in msg for k in ("not found", "no such", "404", "unknown")):
                log.info("colab stop %s: instance already released (%s)", name, e)
                return
            raise

    async def ccu_info(self) -> float | None:
        try:
            out = await self._run("usage", timeout=60)
        except ColabError:
            return None
        return parse_ccu_balance(out)

    async def touch_heartbeat(self, name: str) -> bool:
        try:
            code = "import time; open('/content/kit_heartbeat', 'w').write(str(time.time()))\n"
            await self._run("exec", "-s", name, "--timeout", "20", stdin=code, timeout=25)
            return True
        except Exception as e:
            log.warning("touch_heartbeat via colab exec failed: %s", e)
            return False


def parse_sessions(text: str) -> list[str]:
    """`colab sessions` (0.7.x) prints one line per server-side assignment:
    `[name] endpoint | Hardware: L4 | Shape: ... | Variant: GPU`; `[?]` = assignment with no local name."""
    return [m.group(1) for line in text.splitlines()
            if " | " in line and (m := re.match(r"^\s*\[([^\]]+)\]\s", line))]


def parse_ccu_balance(text: str) -> float | None:
    """Best effort: accept JSON with a balance-like key or a 'balance: 123.4' line."""
    try:
        d = json.loads(text)
        for k in ("currentBalance", "balance", "remaining", "ccu"):
            if k in d:
                return float(d[k])
    except (ValueError, TypeError):
        pass
    m = re.search(r"(balance|remaining)[^0-9]*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    return float(m.group(2)) if m else None
