"""Minimal Chrome DevTools Protocol client (no external deps).

Used by the SVG layout checker to measure real rendered text boxes in a
browser, which is the only way to know whether a label overflows its card —
font metrics differ per platform, so estimating from character counts is
guesswork.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from typing import Any

def _chrome() -> str:
    """First Chrome binary that exists, so the checker works beyond macOS."""
    from pathlib import Path
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


CHROME = _chrome()


class CDP:
    """A CDP session on a headless Chrome instance."""

    def __init__(self, url: str, port: int = 9444, timeout: float = 20.0):
        self.port = port
        args = [CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}",
                "--window-size=1400,1300", "--hide-scrollbars"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            # a container that runs as root cannot start Chrome without this
            args.append("--no-sandbox")
        args.append(url)
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._id = 0
        self.ws = None
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
                pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    self.ws = self._connect(pages[0]["webSocketDebuggerUrl"], timeout)
                    return
            except Exception:
                time.sleep(0.4)
        raise RuntimeError("could not attach to Chrome")

    # ── websocket ──────────────────────────────────────────────────────────
    def _connect(self, url: str, timeout: float) -> socket.socket:
        path = url.split(f":{self.port}", 1)[1]
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        s.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        s.send(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)
        return s

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        n = len(masked)
        if n < 126:
            hdr = bytes([0x81, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x81, 0x80 | 126]) + n.to_bytes(2, "big")
        else:
            hdr = bytes([0x81, 0x80 | 127]) + n.to_bytes(8, "big")
        self.ws.send(hdr + mask + masked)

    def _recv(self) -> dict:
        def rd(n: int) -> bytes:
            b = b""
            while len(b) < n:
                chunk = self.ws.recv(n - len(b))
                if not chunk:
                    raise ConnectionError("socket closed")
                b += chunk
            return b
        h = rd(2)
        ln = h[1] & 0x7F
        if ln == 126:
            ln = int.from_bytes(rd(2), "big")
        elif ln == 127:
            ln = int.from_bytes(rd(8), "big")
        payload = rd(ln)
        if h[1] & 0x80:
            mk = rd(4)
            payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
        return json.loads(payload)

    # ── protocol ───────────────────────────────────────────────────────────
    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        self._id += 1
        mid = self._id
        self._send({"id": mid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._recv()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")
        raise TimeoutError(method)

    def evaluate(self, expression: str, timeout: float = 60.0) -> Any:
        res = self.call("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            raise RuntimeError(json.dumps(res["exceptionDetails"])[:400])
        return res.get("result", {}).get("value")

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
