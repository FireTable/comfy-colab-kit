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


class ColabCLI:
    def __init__(self, binary: str = "colab", token_dir: str = ""):
        self.binary = binary
        self.env = dict(os.environ)
        if token_dir:
            # google-colab-cli keeps its token under ~/.config/colab-cli; point HOME-relative config there
            self.env["XDG_CONFIG_HOME"] = token_dir

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
        out = await self._run("sessions", timeout=60)
        names = []
        for line in out.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s", line)
            if m and m.group(1).lower() not in ("name", "session", "no"):
                names.append(m.group(1))
        return names

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
        await self._run("stop", "-s", name, timeout=180)

    async def ccu_info(self) -> float | None:
        try:
            out = await self._run("ccu-info", timeout=60)
        except ColabError:
            return None
        return parse_ccu_balance(out)


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
