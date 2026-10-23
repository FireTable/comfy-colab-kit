import gzip
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import brotli
import pytest

from gate.proxy import OVERLAY_TAG, Proxy


async def test_proxy_headers_rewrites_accept_encoding():
    p = Proxy("https://comfy-colab.example.com", "/nonexistent", lambda: None, lambda: None, lambda: None)
    # mock request with Accept-Encoding: gzip, deflate, br, zstd
    req = aiohttp.test_utils.make_mocked_request(
        "GET", "/index.html",
        headers={"Accept-Encoding": "gzip, deflate, br, zstd", "Host": "comfy.example.com"}
    )
    h = p._headers(req)
    assert h["Host"] == "comfy-colab.example.com"
    assert h["Accept-Encoding"] == "gzip, deflate, br"


async def test_proxy_decompresses_brotli_html_and_injects_overlay():
    raw_html = b"<html><head><title>ComfyUI</title></head><body><h1>Hello</h1></body></html>"
    compressed_br = brotli.compress(raw_html)

    backend = web.Application()

    async def handle_root(req: web.Request) -> web.Response:
        return web.Response(
            body=compressed_br,
            status=200,
            content_type="text/html",
            headers={"Content-Encoding": "br"}
        )

    backend.router.add_get("/", handle_root)
    backend.router.add_get("/index.html", handle_root)

    server = TestServer(backend)
    await server.start_server()

    try:
        p = Proxy(f"http://127.0.0.1:{server.port}", "/nonexistent", lambda: None, lambda: None, lambda: None)
        gate_app = web.Application()

        async def gate_handler(req: web.Request):
            return await p.handle(req)

        gate_app.router.add_get("/", gate_handler)
        gate_app.router.add_get("/index.html", gate_handler)

        client = TestClient(TestServer(gate_app))
        await client.start_server()
        try:
            res = await client.get("/", headers={"Accept-Encoding": "gzip, deflate, br"})
            assert res.status == 200
            text = await res.text()
            assert OVERLAY_TAG in text
            assert "<h1>Hello</h1>" in text
            assert "Content-Encoding" not in res.headers
        finally:
            await client.close()
            await p.close()
    finally:
        await server.close()


async def test_proxy_decompresses_brotli_stream_asset():
    raw_json = b'{"status": "ok", "items": [1, 2, 3]}'
    compressed_br = brotli.compress(raw_json)

    backend = web.Application()

    async def handle_api(req: web.Request) -> web.Response:
        return web.Response(
            body=compressed_br,
            status=200,
            content_type="application/json",
            headers={"Content-Encoding": "br"}
        )

    backend.router.add_get("/api/test", handle_api)

    server = TestServer(backend)
    await server.start_server()

    try:
        p = Proxy(f"http://127.0.0.1:{server.port}", "/nonexistent", lambda: None, lambda: None, lambda: None)
        gate_app = web.Application()

        async def gate_handler(req: web.Request):
            return await p.handle(req)

        gate_app.router.add_get("/api/test", gate_handler)

        client = TestClient(TestServer(gate_app))
        await client.start_server()
        try:
            res = await client.get("/api/test", headers={"Accept-Encoding": "gzip, deflate, br"})
            assert res.status == 200
            body = await res.read()
            assert body == raw_json
            assert "Content-Encoding" not in res.headers
        finally:
            await client.close()
            await p.close()
    finally:
        await server.close()
