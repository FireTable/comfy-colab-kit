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
    async def session_details(self) -> dict[str, dict[str, str]]: ...
    async def new(self, name: str, accelerator: str, high_mem: bool = False, timeout: float = 180) -> None: ...
    async def upload(self, name: str, src: str, dst: str) -> None: ...
    def exec_stream(self, name: str, code: str, timeout_s: int) -> AsyncIterator[str]: ...
    async def stop(self, name: str) -> None: ...
    async def ccu_info(self) -> float | None: ...
    async def wait_for_kernel(self, name: str, attempts: int = 5) -> bool:
        """Wait until the kernel answers an exec.

        Right after `colab new` + upload the kernel can still be settling; a `colab exec` started
        in that window can hang in the CLI's connect step (the kernel itself is fine - a later exec
        works instantly). Probing with a short timeout keeps a stuck connect from parking the
        whole boot for the full budget.
        """
        for i in range(1, attempts + 1):
            try:
                await self.exec_quick(name, "print('kit-kernel-ready')", timeout_s=45)
                await asyncio.sleep(2)
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("kernel probe %d/%d failed: %s", i, attempts, e)
                await asyncio.sleep(8)
        return False

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
        # Mitigate google-colab-cli #285 / #279: default 10s HTTP/WS timeout causes false failures on cold kernels
        self.env.setdefault("REQUEST_TIMEOUT", "60")

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

    async def session_details(self) -> dict[str, dict[str, str]]:
        try:
            out = await self._run("sessions", timeout=60)
        except ColabError as e:
            msg = str(e).lower()
            if "no active session" in msg or "no session" in msg:
                return {}
            raise
        return parse_session_details(out)

    async def new(self, name: str, accelerator: str, high_mem: bool = False, timeout: float = 180) -> None:
        args = ["new", "-s", name, "--gpu", accelerator]
        if high_mem and accelerator in ("A100", "T4", "CPU"):
            args.append("--high-mem")
        await self._run(*args, timeout=timeout)

    async def upload(self, name: str, src: str, dst: str) -> None:
        await self._run("upload", "-s", name, src, dst, timeout=600)

    async def exec_quick(self, name: str, code: str, timeout_s: int = 60) -> str:
        """One-shot exec with a hard timeout - used to probe whether the kernel is ready."""
        return await self._run("exec", "-s", name, "--timeout", str(timeout_s),
                               stdin=code, timeout=timeout_s + 30)

    async def exec_stream(self, name: str, code: str, timeout_s: int):
        proc = await asyncio.create_subprocess_exec(
            self.binary, "exec", "-s", name, "--timeout", str(timeout_s), env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdin and proc.stdout
        proc.stdin.write(code.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=120)
                except asyncio.TimeoutError as e:
                    proc.kill()
                    raise ColabError("colab exec stream stalled (no output for 120s)") from e
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip("\n")
                yield line
                if line.strip().startswith("RuntimeError: Connection was lost."):
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                    raise ColabError("colab exec connection lost")
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                proc.kill()
                rc = await proc.wait()
            if rc:
                raise ColabError(f"colab exec exited with {rc}")
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

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

    async def wait_for_kernel(self, name: str, attempts: int = 5) -> bool:
        """Wait until the kernel answers an exec.

        Right after `colab new` + upload the kernel can still be settling; a `colab exec` started
        in that window can hang in the CLI's connect step (the kernel itself is fine - a later exec
        works instantly). Probing with a short timeout keeps a stuck connect from parking the
        whole boot for the full budget.
        """
        for i in range(1, attempts + 1):
            try:
                await self.exec_quick(name, "print('kit-kernel-ready')", timeout_s=45)
                await asyncio.sleep(2)
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("kernel probe %d/%d failed: %s", i, attempts, e)
                await asyncio.sleep(8)
        return False

    async def touch_heartbeat(self, name: str) -> bool:
        try:
            code = "import time; open('/content/kit_heartbeat', 'w').write(str(time.time()))\n"
            await self._run("exec", "-s", name, "--timeout", "20", stdin=code, timeout=25)
            return True
        except Exception as e:
            log.warning("touch_heartbeat via colab exec failed: %s", e)
            return False


_FATAL_AUTH_MARKERS = (
    "401",
    "403",
    "re-authenticate",
    "reauthenticate",
    "oauth",
    "unauthorized",
    "invalid_grant",
)
_RETRYABLE_ALLOC_MARKERS = (
    "503",
    "service unavailable",
    "no gpu",
    "capacity",
    "unavailable",
    "timed out",
    "timeout",
    "assignment",
    "quota",
)


def is_retryable_alloc_error(err: Exception | str) -> bool:
    """Return True for transient Colab allocation failures; False for fatal auth or usage errors."""
    msg = str(err).lower()
    if any(m in msg for m in _FATAL_AUTH_MARKERS):
        return False
    if isinstance(err, (TimeoutError, asyncio.TimeoutError)):
        return True
    return any(m in msg for m in _RETRYABLE_ALLOC_MARKERS)


def _detect_gpu_tier(hardware: str, endpoint: str = "") -> str:
    hw = (hardware or "").upper()
    for tier in ("G4", "A100", "H100", "L4", "T4"):
        if re.search(rf"\b{tier}\b", hw):
            return tier
    if "RTX PRO" in hw or "BLACKWELL" in hw:
        return "G4"
    ep = (endpoint or "").lower()
    for tier in ("g4", "a100", "h100", "l4", "t4"):
        if re.search(rf"(?:^|[-_]){tier}(?:[-_]|$)", ep):
            return tier.upper()
    return ""


def parse_sessions(text: str) -> list[str]:
    """`colab sessions` (0.7.x) prints one line per server-side assignment:
    `[name] endpoint | Hardware: L4 | Shape: ... | Variant: GPU`; `[?]` = assignment with no local name."""
    return [m.group(1) for line in text.splitlines()
            if " | " in line and (m := re.match(r"^\s*\[([^\]]+)\]\s", line))]


def parse_session_details(text: str) -> dict[str, dict[str, str]]:
    """Parse `colab sessions` output into `{name: {endpoint, hardware, shape, variant, accelerator}}`."""
    details: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if " | " not in line:
            continue
        m = re.match(r"^\s*\[([^\]]+)\]\s+(.+)$", line)
        if not m:
            continue
        name = m.group(1).strip()
        parts = [p.strip() for p in m.group(2).split("|")]
        if not parts:
            continue
        entry: dict[str, str] = {"endpoint": parts[0]}
        for seg in parts[1:]:
            if ":" in seg:
                k, v = seg.split(":", 1)
                entry[k.strip().lower()] = v.strip()
        accel = _detect_gpu_tier(entry.get("hardware", ""), entry.get("endpoint", ""))
        if accel:
            entry["accelerator"] = accel
        details[name] = entry
    return details


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
