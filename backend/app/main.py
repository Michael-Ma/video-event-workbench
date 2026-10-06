from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .contracts import CreateRunRequest
from .db import Repository, new_id
from .diagnostics import run_diagnostics
from .media import MediaError, generate_demo, probe_video


class APIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details


def artifact_url(path: str) -> str:
    return "/api/artifacts/" + quote(path, safe="/")


def public_media(record: dict) -> dict:
    data = dict(record)
    original = data.get("original_path") or data.get("source_path")
    if original:
        data["original_url"] = artifact_url(original)
    if data.get("preview_path"):
        data["preview_url"] = artifact_url(data["preview_path"])
    return data


def public_results(results: dict | None) -> dict | None:
    if results is None:
        return None
    data = json.loads(json.dumps(results))
    for event in data.get("events", []):
        clip = event.get("clip")
        if clip and clip.get("path"):
            clip["url"] = artifact_url(clip["path"])
    return data


def public_run(run: dict) -> dict:
    return {**run, "results": public_results(run.get("results"))}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    repo = Repository(settings.db_path)
    app = FastAPI(title="Video Event Workbench", version="0.1.0")
    app.state.settings = settings
    app.state.repo = repo

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        error = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            error["details"] = exc.details
        return JSONResponse({"error": error}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "invalid_request", "message": "请求参数无效。",
                                      "details": json.loads(json.dumps(exc.errors(), default=str))}},
                            status_code=422)

    @app.exception_handler(KeyError)
    async def missing_record(_request: Request, _exc: KeyError):
        return JSONResponse({"error": {"code": "not_found", "message": "记录不存在。"}},
                            status_code=404)

    @app.exception_handler(MediaError)
    async def media_error(_request: Request, exc: MediaError):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc),
                                      "details": getattr(exc, "details", {})}}, status_code=422)

    @app.get("/api/health")
    def health():
        heartbeat = repo.get_meta("worker_heartbeat")
        alive = False
        if heartbeat:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(heartbeat)).total_seconds()
                alive = 0 <= age < 20
            except (ValueError, TypeError):
                pass
        return {"status": "ok", "api_key_configured": bool(settings.gemini_api_key),
                "default_model": settings.gemini_model, "worker_alive": alive,
                "ffmpeg_available": bool(shutil.which("ffmpeg")),
                "ffprobe_available": bool(shutil.which("ffprobe"))}

    @app.get("/api/media")
    def list_media():
        return [public_media(record) for record in repo.list_media()]

    @app.get("/api/media/{media_id}")
    def get_media(media_id: str):
        return public_media(repo.get_media(media_id))

    @app.post("/api/media", status_code=201)
    async def upload(file: Annotated[UploadFile, File()]):
        media_id = new_id("media")
        folder = settings.data_dir / "media" / media_id
        folder.mkdir(parents=True)
        filename = Path((file.filename or "video").replace("\\", "/")).name[:240] or "video"
        suffix = Path(filename).suffix.lower()
        if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            suffix = ".upload"
        target = folder / ("original" + suffix)
        size = 0
        try:
            with target.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise APIError("upload_too_large", f"视频超过 {settings.max_upload_mb} MB 限制。", 413)
                    out.write(chunk)
            if not size:
                raise APIError("empty_upload", "上传的视频为空。", 422)
            metadata = await run_in_threadpool(probe_video, target)
            if metadata.get("duration_us", 0) <= 0:
                raise APIError("invalid_video", "视频没有可用时长。", 422)
            original = target.relative_to(settings.data_dir).as_posix()
            record = repo.create_media({"id": media_id, "filename": filename, "status": "ready",
                                        "original_path": original, "source_path": original,
                                        "size_bytes": size, "is_demo": False, "metadata": metadata,
                                        **metadata})
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            await file.close()
        return public_media(record)

    @app.post("/api/demo", status_code=201)
    def demo():
        for record in repo.list_media():
            source = record.get("original_path") or record.get("source_path")
            if record.get("is_demo") and source and (settings.data_dir / source).is_file():
                return public_media(record)
        media_id = new_id("media")
        folder = settings.data_dir / "media" / media_id
        folder.mkdir(parents=True)
        target = folder / "demo.mp4"
        try:
            fixture = generate_demo(target)
            metadata = probe_video(target)
            source = target.relative_to(settings.data_dir).as_posix()
            record = repo.create_media({"id": media_id, "filename": "内置流程测试.mp4",
                                        "status": "ready", "is_demo": True,
                                        "original_path": source, "source_path": source,
                                        "metadata": metadata, **metadata,
                                        "fixture_events": fixture.get("fixture_events", [])})
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return public_media(record)

    @app.post("/api/runs", status_code=202)
    def create_run(body: CreateRunRequest, idempotency_key: str | None = Header(default=None)):
        media = repo.get_media(body.media_id)
        if media.get("status") != "ready":
            raise APIError("media_not_ready", "视频尚未准备好。", 409)
        if not body.query.strip():
            raise APIError("empty_query", "请输入要定位的事件。", 422)
        if body.config.provider == "gemini" and not settings.gemini_api_key:
            raise APIError("api_key_missing", "请在本地 .env 中配置 GEMINI_API_KEY，然后重启服务。", 409)
        if body.config.provider == "fixture" and not media.get("is_demo"):
            raise APIError("fixture_requires_demo", "流程测试模式只支持内置示例视频。", 422)
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
            raise APIError("invalid_idempotency_key", "无效的请求标识。", 422)
        payload = body.model_dump()
        if "model_id" not in body.config.model_fields_set:
            payload["config"]["model_id"] = settings.gemini_model
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        try:
            run, created = repo.create_run(payload, idempotency_key, request_hash)
        except ValueError as exc:
            raise APIError("idempotency_conflict", "同一请求标识已用于不同参数。", 409) from exc
        if created:
            repo.log(run["id"], "queued", "RUN_CREATED", "任务已创建，等待 worker。")
        return public_run(run)

    @app.get("/api/runs")
    def list_runs(limit: int = Query(default=30, ge=1, le=200)):
        return [public_run(run) for run in repo.list_runs(limit)]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        run = repo.get_run(run_id)
        tasks = repo.list_tasks(run_id)
        return {**public_run(run), "tasks": tasks, "diagnostics": run_diagnostics(run, tasks)}

    @app.get("/api/runs/{run_id}/logs")
    def get_logs(run_id: str, after: int = Query(default=0, ge=0),
                 limit: int = Query(default=200, ge=1, le=1000)):
        repo.get_run(run_id)
        return {"items": repo.get_logs(run_id, after, limit)}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        run = repo.cancel_run(run_id)
        repo.log(run_id, run["stage"], "CANCEL_REQUESTED", "已请求停止任务。")
        return public_run(run)

    @app.get("/api/runs/{run_id}/results")
    def get_results(run_id: str):
        results = repo.get_run(run_id).get("results")
        if results is None:
            raise APIError("results_not_ready", "该任务还没有结果。", 409)
        return public_results(results)

    @app.get("/api/artifacts/{relative_path:path}")
    def get_artifact(relative_path: str):
        parts = Path(relative_path).parts
        if not parts or parts[0] not in {"media", "runs"} or ".." in parts:
            raise APIError("invalid_artifact_path", "无效的文件路径。", 404)
        target = (settings.data_dir / relative_path).resolve()
        if not target.is_relative_to(settings.data_dir.resolve()) or not target.is_file():
            raise APIError("artifact_not_found", "文件不存在。", 404)
        return FileResponse(target)

    return app


app = create_app()
