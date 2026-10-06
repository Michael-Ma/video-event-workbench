"""Validate prerequisites and supervise API, worker and web process groups."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://127.0.0.1:8000/api/health"
WEB_URL = "http://127.0.0.1:5173"


class StartupError(RuntimeError):
    pass


def configuration(root: Path = ROOT, *, require_api_key: bool = False):
    from app.config import Settings
    from dotenv import load_dotenv

    env_path = root / ".env"
    try:
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as target:
            target.write((root / ".env.example").read_bytes())
        print("[配置] 已创建 .env；已有配置不会被覆盖。", flush=True)
    load_dotenv(env_path, override=False)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key.lower() in {"your_api_key", "your-api-key", "replace_me", "changeme"}:
        raise StartupError("GEMINI_API_KEY 仍是占位值，请填写实际密钥或留空使用示例。")
    if require_api_key and not key:
        raise StartupError("未配置 GEMINI_API_KEY。请填写本地 .env 后重试。")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    if not model or len(model) > 256 or any(char.isspace() for char in model):
        raise StartupError("GEMINI_MODEL 必须是非空且不含空白的模型 ID。")
    folder = os.getenv("VEW_DATA_DIR", ".data").strip()
    if not folder:
        raise StartupError("VEW_DATA_DIR 不能为空。")
    data_dir = Path(folder).expanduser()
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    try:
        upload_mb = int(os.getenv("VEW_MAX_UPLOAD_MB", "2048"))
        if upload_mb <= 0:
            raise ValueError()
    except ValueError as exc:
        raise StartupError("VEW_MAX_UPLOAD_MB 必须是正整数。") from exc
    settings = Settings(data_dir.resolve(), key, model, upload_mb)
    try:
        settings.ensure_dirs()
        with tempfile.TemporaryFile(dir=settings.data_dir):
            pass
    except OSError as exc:
        raise StartupError("VEW_DATA_DIR 不可写，请检查目录与权限。") from exc
    env = {**os.environ, "PYTHONPATH": str(root / "backend"), "GEMINI_API_KEY": key,
           "GEMINI_MODEL": model, "VEW_DATA_DIR": str(settings.data_dir),
           "VEW_MAX_UPLOAD_MB": str(upload_mb)}
    print(f"[配置] 模型：{model}；数据目录：{settings.data_dir}；上传上限：{upload_mb} MB",
          flush=True)
    print("[配置] GEMINI_API_KEY 已配置（未进行远端验证）。" if key else
          "[配置] 未配置 GEMINI_API_KEY：可使用内置示例，真实识别需配置后重启。", flush=True)
    return settings, env


def prerequisites(root: Path = ROOT):
    import av

    missing = [name for name in ("node", "npm", "ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing or not (root / "web/node_modules/.bin/vite").is_file():
        raise StartupError("缺少运行依赖，请使用 ./start.sh 自动安装。")
    version = subprocess.check_output(["node", "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        raise StartupError("需要 Node.js 22 或以上，请使用 ./start.sh 自动安装。")
    encoders = subprocess.check_output(["ffmpeg", "-hide_banner", "-encoders"], text=True,
                                      stderr=subprocess.DEVNULL)
    if "libx264" not in encoders:
        raise StartupError("当前 FFmpeg 缺少 libx264 编码器，请安装完整 FFmpeg。")
    try:
        av.CodecContext.create("libx264", "w")
    except av.FFmpegError as exc:
        raise StartupError("当前 PyAV 缺少 libx264 编码器，请重新安装锁定依赖。") from exc
    print(f"[环境] Python {sys.version.split()[0]}；Node {version}；FFmpeg/PyAV 编码器就绪。",
          flush=True)


def available_ports(ports=(8000, 5173)):
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
                probe.listen(1)
            except OSError as exc:
                raise StartupError(f"本地端口 {port} 无法使用。请先停止占用它的服务后重试。") from exc


def services_ready(api_url=API_URL, web_url=WEB_URL) -> bool:
    # Local checks must not route through an inherited network proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(api_url, timeout=1) as response:
            health = json.load(response)
        if health.get("status") != "ok" or health.get("worker_alive") is not True:
            return False
        with opener.open(web_url, timeout=1) as response:
            return response.status == 200
    except (OSError, HTTPError, URLError, ValueError):
        return False


def stop_children(children):
    # Stop our process groups, including Vite if its npm parent has already exited.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def serve(env: dict, root: Path = ROOT, *, ready_timeout: float = 45) -> int:
    web_env = {name: value for name, value in env.items() if name != "GEMINI_API_KEY"}
    commands = [
        ("API", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--no-access-log"], env),
        ("worker", [sys.executable, "-m", "app.worker"], env),
        ("网页", ["npm", "--prefix", "web", "run", "dev", "--", "--strictPort", "--clearScreen",
                "false"], web_env),
    ]
    children = []
    stopped = False

    def stop(_signum=None, _frame=None):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for _name, command, child_env in commands:
            child = subprocess.Popen(command, cwd=root, env=child_env, start_new_session=True)
            children.append(child)
        print("[启动] 等待 API、worker 与网页就绪…", flush=True)
        deadline, ready = time.monotonic() + ready_timeout, False
        while not stopped:
            for (name, _, _), child in zip(commands, children):
                if child.poll() is not None:
                    raise StartupError(f"{name} 服务提前退出（代码 {child.returncode}）。")
            if not ready:
                if services_ready():
                    ready = True
                    print(f"\n[就绪] {WEB_URL}\n按 Ctrl+C 停止全部服务。\n", flush=True)
                elif time.monotonic() >= deadline:
                    raise StartupError("服务启动超时；请检查上方服务错误。已停止本次启动的所有服务。")
            time.sleep(0.25)
    finally:
        stop_children(children)
    print("[停止] 本次启动的全部服务已退出。", flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="检查本地环境并启动服务；自动安装请使用 ./start.sh")
    parser.add_argument("--check", action="store_true", help="只检查，不启动服务")
    parser.add_argument("--require-api-key", action="store_true", help="必须配置 GEMINI_API_KEY")
    args = parser.parse_args(argv)
    try:
        prerequisites()
        _, env = configuration(require_api_key=args.require_api_key)
        available_ports()
        if args.check:
            print("[通过] 启动检查全部通过。", flush=True)
            return 0
        return serve(env)
    except (StartupError, OSError, subprocess.SubprocessError) as exc:
        print(f"[失败] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
