import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("startup", Path(__file__).parents[2] / "scripts/dev.py")
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


@pytest.fixture
def config_root(tmp_path, monkeypatch):
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "VEW_DATA_DIR", "VEW_MAX_UPLOAD_MB"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\nGEMINI_MODEL=gemini-3.8-flash\n")
    return tmp_path


def test_missing_key_allows_demo_but_strict_mode_fails(config_root):
    settings, _ = startup.configuration(config_root)
    assert not settings.gemini_api_key
    assert (config_root / ".env").stat().st_mode & 0o777 == 0o600
    with pytest.raises(startup.StartupError, match="未配置 GEMINI_API_KEY"):
        startup.configuration(config_root, require_api_key=True)


def test_existing_dotenv_is_preserved_export_wins_and_secret_never_prints(config_root, monkeypatch,
                                                                       capsys):
    original = "GEMINI_API_KEY=file-test-secret\nGEMINI_MODEL=configured-model\n"
    (config_root / ".env").write_text(original)
    monkeypatch.setenv("GEMINI_API_KEY", "export-test-secret")
    settings, env = startup.configuration(config_root, require_api_key=True)
    assert settings.gemini_api_key == env["GEMINI_API_KEY"] == "export-test-secret"
    assert settings.gemini_model == "configured-model"
    assert (config_root / ".env").read_text() == original
    output = capsys.readouterr().out
    assert "file-test-secret" not in output and "export-test-secret" not in output


@pytest.mark.parametrize("name,value,message", [
    ("VEW_MAX_UPLOAD_MB", "0", "VEW_MAX_UPLOAD_MB"),
    ("VEW_MAX_UPLOAD_MB", "not-a-number", "VEW_MAX_UPLOAD_MB"),
    ("VEW_DATA_DIR", "", "VEW_DATA_DIR"),
    ("GEMINI_MODEL", "", "GEMINI_MODEL"),
])
def test_invalid_configuration_stops_before_launch(config_root, monkeypatch, name, value, message):
    monkeypatch.setenv(name, value)
    with pytest.raises(startup.StartupError, match=message):
        startup.configuration(config_root)


def test_occupied_port_cannot_be_reported_ready():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(startup.StartupError, match=str(port)):
            startup.available_ports([port])
    startup.available_ports([port])


def test_readiness_requires_worker_and_frontend(monkeypatch):
    state = {"worker_alive": False, "web_status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/api/health" else state["web_status"])
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "worker_alive": state["worker_alive"]}).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    try:
        assert not startup.services_ready(url + "/api/health", url)
        state["worker_alive"] = True
        assert startup.services_ready(url + "/api/health", url)
        state["web_status"] = 503
        assert not startup.services_ready(url + "/api/health", url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
