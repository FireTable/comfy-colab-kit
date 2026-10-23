"""Reverse proxy from the public hostname to ComfyUI behind the Colab tunnel.

Adds Cloudflare Access service-token headers, rewrites Host/Origin so ComfyUI's
origin check passes, proxies WebSockets (/ws), and injects the overlay script
into ComfyUI's index.html.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

import aiohttp
import yarl
from aiohttp import web

from .config import read_secret_file

log = logging.getLogger("gate.proxy")

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length", "content-encoding"}
#: never forward the user's Access credentials to the backend
STRIP_REQ = {"cf-access-jwt-assertion", "cf-access-authenticated-user-email", "cookie", "authorization",
             "cf-connecting-ip", "x-forwarded-for", "cf-ray", "cf-ipcountry", "cf-visitor"}
OVERLAY_TAG = '<script src="/_gate/static/overlay.js" defer></script>'


def service_headers(path: str) -> dict[str, str]:
    raw = read_secret_file(path)
    if not raw:
        return {}
    d = json.loads(raw)
    return {"CF-Access-Client-Id": d["client_id"], "CF-Access-Client-Secret": d["client_secret"]}


def inject_overlay(html: str) -> str:
    if OVERLAY_TAG in html:
        return html
    i = html.lower().rfind("</body>")
    return html[:i] + OVERLAY_TAG + html[i:] if i >= 0 else html + OVERLAY_TAG


class Proxy:
    def __init__(self, backend_url: str, token_file: str, on_activity: Callable[[], None],
                 on_ws_open: Callable[[], None], on_ws_close: Callable[[], None]):
        self.backend = backend_url.rstrip("/")
        self.token_file = token_file
        self.on_activity, self.on_ws_open, self.on_ws_close = on_activity, on_ws_open, on_ws_close
        self._client: aiohttp.ClientSession | None = None

    async def client(self) -> aiohttp.ClientSession:
        if self._client is None or self._client.closed:
            self._client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15),
                                                 auto_decompress=True)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    def _headers(self, req: web.Request) -> dict[str, str]:
        h = {k: v for k, v in req.headers.items() if k.lower() not in HOP_BY_HOP | STRIP_REQ}
        backend_host = self.backend.split("://", 1)[1]
        h["Host"] = backend_host
        if "Origin" in h:
            h["Origin"] = self.backend  # ComfyUI rejects Host/Origin mismatches
        h.update(service_headers(self.token_file))
        return h

    async def probe(self) -> bool:
        try:
            c = await self.client()
            async with c.get(self.backend + "/system_stats", headers=service_headers(self.token_file),
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                return r.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def queue_busy(self) -> bool:
        try:
            c = await self.client()
            async with c.get(self.backend + "/queue", headers=service_headers(self.token_file),
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                q = await r.json()
                return bool(q.get("queue_running") or q.get("queue_pending"))
        except Exception:  # noqa: BLE001
            return False

    async def heartbeat(self) -> None:
        """Tell the Colab-side watchdog that the gate is alive."""
        try:
            c = await self.client()
            async with c.post(self.backend + "/kit/heartbeat", headers=service_headers(self.token_file),
                              timeout=aiohttp.ClientTimeout(total=8)):
                pass
        except Exception:  # noqa: BLE001
            pass

    async def handle(self, req: web.Request) -> web.StreamResponse:
        self.on_activity()
        if req.headers.get("Upgrade", "").lower() == "websocket":
            return await self._ws(req)
        c = await self.client()
        # raw_path keeps percent-encoding: the frontend loads /api/userdata/workflows%2Fx.json and ComfyUI's
        # route only matches when %2F stays encoded (rel_url.path_qs would decode it to a 404)
        url = yarl.URL(self.backend + req.raw_path, encoded=True)
        body = await req.read() if req.can_read_body else None
        async with c.request(req.method, url, headers=self._headers(req), data=body, allow_redirects=False) as r:
            ctype = r.headers.get("Content-Type", "")
            headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP_BY_HOP}
            if req.rel_url.path in ("/", "/index.html") and "text/html" in ctype:
                html = inject_overlay(await r.text())
                return web.Response(status=r.status, text=html, content_type="text/html", headers={
                    k: v for k, v in headers.items() if k.lower() not in ("content-type",)})
            resp = web.StreamResponse(status=r.status, headers=headers)
            await resp.prepare(req)
            async for chunk in r.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp

    async def _ws(self, req: web.Request) -> web.WebSocketResponse:
        ws_client = web.WebSocketResponse(heartbeat=30, max_msg_size=0)
        await ws_client.prepare(req)
        c = await self.client()
        url = yarl.URL(self.backend.replace("https://", "wss://").replace("http://", "ws://") + req.raw_path,
                       encoded=True)
        self.on_ws_open()
        try:
            # aiohttp builds its own handshake; forwarding the client's Sec-WebSocket-* headers duplicates them
            hdrs = {k: v for k, v in self._headers(req).items() if not k.lower().startswith("sec-websocket")}
            async with c.ws_connect(url, headers=hdrs, heartbeat=30, max_msg_size=0) as ws_back:
                async def pump(src, dst, is_client):
                    async for msg in src:
                        if is_client:
                            self.on_activity()
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await dst.send_str(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await dst.send_bytes(msg.data)
                        else:
                            break
                tasks = [asyncio.create_task(pump(ws_client, ws_back, True)),
                         asyncio.create_task(pump(ws_back, ws_client, False))]
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        except aiohttp.ClientError as e:
            log.warning("backend ws failed: %s", e)
        finally:
            self.on_ws_close()
            await ws_client.close()
        return ws_client
