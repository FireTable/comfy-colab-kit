import http.server
import json
import stat
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from ckit import cli


def _start_mock_server(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_build_auth_url():
    url = cli.build_auth_url(
        client_id="cid-123",
        redirect_uri="http://127.0.0.1:4321/callback",
        scope="https://www.googleapis.com/auth/drive.file",
        state="st-xyz",
    )
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    qs = urllib.parse.parse_qs(parsed.query)
    assert qs["client_id"] == ["cid-123"]
    assert qs["redirect_uri"] == ["http://127.0.0.1:4321/callback"]
    assert qs["response_type"] == ["code"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]
    assert qs["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert qs["state"] == ["st-xyz"]


def test_exchange_auth_code_against_mock_server():
    captured = {}

    class _TokenHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            captured["path"] = self.path
            captured["form"] = urllib.parse.parse_qs(body)
            payload = json.dumps({
                "access_token": "tok-access-mock",
                "refresh_token": "tok-refresh-mock",
                "scope": "https://www.googleapis.com/auth/drive.file",
                "token_type": "Bearer",
                "expires_in": 3600,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    srv, base = _start_mock_server(_TokenHandler)
    try:
        res = cli.exchange_auth_code(
            code="auth-code-99",
            client_id="cid-test",
            client_secret="sec-test",
            redirect_uri="http://127.0.0.1:9999/callback",
            token_uri=f"{base}/token",
        )
        assert captured["path"] == "/token"
        assert captured["form"]["grant_type"] == ["authorization_code"]
        assert captured["form"]["code"] == ["auth-code-99"]
        assert captured["form"]["client_id"] == ["cid-test"]
        assert captured["form"]["client_secret"] == ["sec-test"]
        assert captured["form"]["redirect_uri"] == ["http://127.0.0.1:9999/callback"]
        assert res["refresh_token"] == "tok-refresh-mock"
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_gdrive_auth_loopback_and_permissions(tmp_path, monkeypatch, capsys):
    secret_val = "sec-mock-val"
    refresh_val = "ref-mock-val"
    access_val = "acc-mock-val"

    class _TokenHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            _ = self.rfile.read(length)
            payload = json.dumps({
                "access_token": access_val,
                "refresh_token": refresh_val,
                "scope": "https://www.googleapis.com/auth/drive.file",
                "expires_in": 3600,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    srv, base = _start_mock_server(_TokenHandler)
    out_file = tmp_path / "secrets" / "gdrive_oauth.json"

    def _fake_open(url):
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        cb_base = qs["redirect_uri"][0]
        st = qs["state"][0]

        def _hit():
            cb_url = f"{cb_base}?code=cb-code-123&state={urllib.parse.quote(st)}"
            with urllib.request.urlopen(cb_url, timeout=5) as r:
                assert r.status == 200

        threading.Thread(target=_hit, daemon=True).start()
        return True

    import webbrowser
    monkeypatch.setattr(webbrowser, "open", _fake_open)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ckit",
            "gdrive-auth",
            "--client-id",
            "cid-loopback",
            "--client-secret",
            secret_val,
            "--out",
            str(out_file),
            "--token-uri",
            f"{base}/token",
        ],
    )
    try:
        cli.main()
    finally:
        srv.shutdown()
        srv.server_close()

    captured = capsys.readouterr()
    assert captured.out.strip() == str(out_file)
    for secret_str in (secret_val, refresh_val, access_val):
        assert secret_str not in captured.out
        assert secret_str not in captured.err

    assert out_file.is_file()
    mode = stat.S_IMODE(out_file.stat().st_mode)
    assert mode == 0o600
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["client_id"] == "cid-loopback"
    assert data["client_secret"] == secret_val
    assert data["refresh_token"] == refresh_val
    assert data["scope"] == "https://www.googleapis.com/auth/drive.file"


def test_cli_gdrive_auth_no_browser_code_fallback(tmp_path, monkeypatch, capsys):
    secret_val = "sec-headless"
    refresh_val = "ref-headless"

    class _TokenHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            _ = self.rfile.read(length)
            payload = json.dumps({
                "access_token": "acc-headless",
                "refresh_token": refresh_val,
                "scope": "https://www.googleapis.com/auth/drive.file",
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    srv, base = _start_mock_server(_TokenHandler)
    out_file = tmp_path / "gdrive.json"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid-env")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", secret_val)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ckit",
            "gdrive-auth",
            "--no-browser",
            "--code",
            "http://127.0.0.1/callback?code=pasted-code-77",
            "--out",
            str(out_file),
            "--token-uri",
            f"{base}/token",
        ],
    )
    try:
        cli.main()
    finally:
        srv.shutdown()
        srv.server_close()

    captured = capsys.readouterr()
    assert captured.out.strip() == str(out_file)
    assert secret_val not in captured.out
    assert refresh_val not in captured.out
    assert stat.S_IMODE(out_file.stat().st_mode) == 0o600


def test_cli_jobs_table_and_json(monkeypatch, capsys):
    seen_queries = []

    class _JobsHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            seen_queries.append((parsed.path, urllib.parse.parse_qs(parsed.query)))
            payload = json.dumps({
                "jobs": [
                    {
                        "id": "job_001",
                        "status": "completed",
                        "profile_id": "moss-image",
                        "pinned": True,
                        "elapsed_s": 12.34,
                        "prompt_text": "cinematic neon cityscape in rain",
                    },
                    {
                        "id": "job_002",
                        "status": "queued",
                        "profile_id": "flux-klein",
                        "pinned": False,
                        "elapsed_s": None,
                        "prompt_text": "minimalist glass sculpture",
                    },
                ],
                "total": 2,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    srv, base = _start_mock_server(_JobsHandler)
    try:
        monkeypatch.setenv("CKIT_URL", base)
        monkeypatch.setattr(
            sys,
            "argv",
            ["ckit", "jobs", "--profile", "moss-image", "--status", "completed", "--pinned", "--limit", "10"],
        )
        cli.main()
        out_table = capsys.readouterr().out
        assert "job_001" in out_table
        assert "completed" in out_table
        assert "moss-image" in out_table
        assert "12.3s" in out_table
        assert "cinematic neon cityscape" in out_table
        assert seen_queries[-1][0] == "/_gate/jobs"
        assert seen_queries[-1][1] == {
            "profile": ["moss-image"],
            "status": ["completed"],
            "pinned": ["1"],
            "limit": ["10"],
        }

        monkeypatch.setattr(sys, "argv", ["ckit", "jobs", "--json"])
        cli.main()
        out_json = json.loads(capsys.readouterr().out)
        assert out_json["total"] == 2
        assert len(out_json["jobs"]) == 2
        assert seen_queries[-1][1] == {"limit": ["20"]}
    finally:
        srv.shutdown()
        srv.server_close()


def test_mcp_server_11_tools_and_offline_queue_handlers(tmp_path, monkeypatch):
    from ckit import mcp_server

    names = [t["name"] for t in mcp_server.TOOLS]
    assert len(names) == 11
    for expected in (
        "gate_status",
        "gate_wake",
        "gate_txt2img",
        "gate_img2img",
        "gate_generate",
        "gate_stop",
        "gate_queue_job",
        "gate_list_jobs",
        "gate_get_job",
        "gate_manage_job",
        "gate_storage_status",
    ):
        assert expected in names

    requests_log = []
    img_file = tmp_path / "ref_input.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    class _MockGateHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            requests_log.append(("GET", parsed.path, qs))
            if parsed.path == "/_gate/jobs":
                body = {
                    "jobs": [
                        {
                            "id": "job_abc",
                            "status": "completed",
                            "profile_id": "moss-image",
                            "pinned": False,
                            "outputs": [
                                {
                                    "id": "ast_1",
                                    "filename": "out.png",
                                    "url": "/_gate/archive/assets/ast_1",
                                    "thumb_url": "/_gate/archive/thumbs/ast_1",
                                }
                            ],
                        }
                    ],
                    "total": 1,
                }
            elif parsed.path == "/_gate/jobs/job_abc":
                body = {
                    "id": "job_abc",
                    "status": "completed",
                    "profile_id": "moss-image",
                    "prompt": {"1": {"class_type": "SaveImage"}},
                    "outputs": [
                        {
                            "id": "ast_1",
                            "filename": "out.png",
                            "url": "/_gate/archive/assets/ast_1",
                            "thumb_url": "/_gate/archive/thumbs/ast_1",
                        }
                    ],
                }
            elif parsed.path == "/_gate/storage/status":
                body = {"backend": "gdrive", "last_backup_at": 1700000000.0, "counts": {"queued": 1}}
            elif parsed.path == "/_gate/archive/profiles":
                body = {"profiles": [{"profile_id": "moss-image", "count": 1}], "totals": {"completed": 1}}
            else:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length)
            if parsed.path == "/upload/image":
                requests_log.append(("POST", parsed.path, None))
                err_raw = json.dumps({"error": "asleep", "message": "sleeping"}).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_raw)))
                self.end_headers()
                self.wfile.write(err_raw)
                return
            if parsed.path == "/_gate/jobs/upload":
                requests_log.append(("POST", parsed.path, None))
                res = {"id": "ast_in_1", "name": "ref_input.png", "filename": "ref_input.png"}
                status = 201
            else:
                data = json.loads(raw_body.decode("utf-8") or "{}") if raw_body else {}
                requests_log.append(("POST", parsed.path, data))
                if parsed.path == "/_gate/jobs":
                    res = {
                        "id": "job_new_1",
                        "client_prompt_id": "cpid_1",
                        "status": "queued",
                        "profile_id": data.get("profile") or "moss-image",
                    }
                    status = 202
                elif parsed.path == "/_gate/jobs/job_abc/pin":
                    res = {"id": "job_abc", "pinned": data.get("pinned")}
                    status = 200
                elif parsed.path in ("/_gate/jobs/job_abc/retry", "/_gate/jobs/job_abc/cancel"):
                    res = {"id": "job_abc", "status": "queued" if parsed.path.endswith("/retry") else "canceled"}
                    status = 200
                elif parsed.path == "/_gate/storage/backup":
                    res = {"ok": True, "backend": "gdrive"}
                    status = 200
                else:
                    res = {}
                    status = 404
            raw = json.dumps(res).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_DELETE(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            requests_log.append(("DELETE", parsed.path, qs))
            raw = json.dumps({"deleted": True, "id": "job_abc"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            return

    srv, base = _start_mock_server(_MockGateHandler)
    try:
        monkeypatch.setenv("CKIT_URL", base)

        # 1. upload_image_to_gate fallback on 503 asleep -> /_gate/jobs/upload
        ok, uploaded_name = mcp_server.upload_image_to_gate(str(img_file), allow_offline=False)
        assert ok is True
        assert uploaded_name == "ref_input.png"
        assert ("POST", "/upload/image", None) in requests_log
        assert ("POST", "/_gate/jobs/upload", None) in requests_log

        # 2. gate_queue_job with offline image upload
        q_res = mcp_server.handle_tool_call(
            "gate_queue_job",
            {"prompt": {"1": {"class_type": "SaveImage"}}, "profile": "flux-klein", "image": str(img_file)},
        )
        assert not q_res.get("isError")
        q_data = json.loads(q_res["content"][0]["text"])
        assert q_data["job_id"] == "job_new_1"
        assert q_data["status"] == "queued"
        assert q_data["profile_id"] == "flux-klein"
        post_job_call = [r for r in requests_log if r[0] == "POST" and r[1] == "/_gate/jobs"][-1]
        assert post_job_call[2]["input_asset_ids"] == ["ast_in_1"]

        # 3. gate_list_jobs absolutizes URLs
        l_res = mcp_server.handle_tool_call("gate_list_jobs", {"profile": "moss-image", "pinned": True, "limit": 5})
        l_data = json.loads(l_res["content"][0]["text"])
        assert l_data["jobs"][0]["outputs"][0]["url"] == f"{base}/_gate/archive/assets/ast_1"
        assert l_data["jobs"][0]["outputs"][0]["thumb_url"] == f"{base}/_gate/archive/thumbs/ast_1"

        # 4. gate_get_job respects include_prompt=False by default and True when asked
        g_res = mcp_server.handle_tool_call("gate_get_job", {"job_id": "job_abc"})
        g_data = json.loads(g_res["content"][0]["text"])
        assert "prompt" not in g_data
        assert g_data["outputs"][0]["url"] == f"{base}/_gate/archive/assets/ast_1"

        g_res2 = mcp_server.handle_tool_call("gate_get_job", {"job_id": "job_abc", "include_prompt": True})
        assert "prompt" in json.loads(g_res2["content"][0]["text"])

        # 5. gate_manage_job actions
        for action, extra in (
            ("pin", {}),
            ("unpin", {}),
            ("retry", {}),
            ("cancel", {}),
            ("delete", {"delete_files": True}),
        ):
            m_res = mcp_server.handle_tool_call("gate_manage_job", {"job_id": "job_abc", "action": action, **extra})
            assert not m_res.get("isError")
        assert ("DELETE", "/_gate/jobs/job_abc", {"delete_files": ["1"]}) in requests_log

        # 6. gate_storage_status with backup_now=True
        s_res = mcp_server.handle_tool_call("gate_storage_status", {"backup_now": True})
        s_data = json.loads(s_res["content"][0]["text"])
        assert s_data["backup"]["ok"] is True
        assert s_data["storage"]["backend"] == "gdrive"
        assert s_data["profiles"][0]["profile_id"] == "moss-image"
    finally:
        srv.shutdown()
        srv.server_close()
