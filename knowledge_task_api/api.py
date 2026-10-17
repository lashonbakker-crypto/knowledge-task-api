"""Authenticated API, bounded JSON writes and replayable task events."""

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .config import Principal, Settings
from .store import Conflict, Missing, Store, TERMINAL
from .worker import Worker

logger = logging.getLogger("knowledge_task_api.requests")
MAX_CURSOR = 2**63 - 1


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=64000)
    max_attempts: int = Field(default=3, ge=1, le=5, strict=True)

    @field_validator("title", "text")
    @classmethod
    def valid_text(cls, value):
        if not value.strip():
            raise ValueError("text cannot be blank")
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("text must encode as UTF-8") from error
        return value


class RequestGuard:
    """Bound write bodies before JSON parsing and log metadata only."""

    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        candidate = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = candidate if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", candidate) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started, status = time.perf_counter(), 500

        async def tagged_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [(b"x-request-id", request_id.encode())]
            await send(message)

        try:
            if scope["method"] in {"POST", "PUT", "PATCH"}:
                chunks, size = [], 0
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > self.max_bytes:
                        return await JSONResponse({"detail": "request body too large"}, status_code=413)(scope, receive, tagged_send)
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
                body, replayed = b"".join(chunks), False

                async def replay_receive():
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()

                await self.app(scope, replay_receive, tagged_send)
            else:
                await self.app(scope, receive, tagged_send)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(json.dumps({"request_id": request_id, "method": scope["method"], "route": route, "status": status, "duration_ms": round((time.perf_counter()-started)*1000, 3)}))


def create_app(settings: Settings | None = None, *, store: Store | None = None,
               processor=None, processor_with_context=None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(settings.database, lease_seconds=settings.lease_seconds)
    worker = Worker(store, processor=processor, processor_with_context=processor_with_context,
                    max_processing_seconds=settings.processing_seconds)

    @asynccontextmanager
    async def lifespan(app):
        if settings.worker_enabled:
            worker.start()
        try:
            yield
        finally:
            if settings.worker_enabled:
                await asyncio.to_thread(worker.stop)

    app = FastAPI(title="Knowledge Task API", version=__version__, lifespan=lifespan)
    app.state.store, app.state.worker = store, worker
    app.add_middleware(RequestGuard, max_bytes=settings.max_request_bytes)
    bearer = HTTPBearer(auto_error=False)

    def principal(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        identity = settings.authenticate(credentials.credentials) if credentials else None
        if identity is None:
            raise HTTPException(401, "valid Bearer token required", headers={"WWW-Authenticate": "Bearer"})
        return identity

    Identity = Annotated[Principal, Depends(principal)]

    @app.exception_handler(Missing)
    async def missing_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(sqlite3.OperationalError)
    async def storage_handler(request, error):
        return JSONResponse({"detail": "storage temporarily unavailable"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        # Do not reflect documents or malformed Unicode in validation errors.
        def safe(value):
            return str(value).encode("utf-8", errors="backslashreplace").decode("utf-8")
        details = [{"loc": [safe(part) for part in item["loc"]], "msg": safe(item["msg"]), "type": safe(item["type"])} for item in error.errors()]
        return JSONResponse({"detail": details}, status_code=422)

    @app.get("/health")
    def health():
        with store.connection() as connection:
            connection.execute("SELECT 1 FROM tasks LIMIT 1").fetchone()
        return {"status": "ok", "version": __version__}

    @app.post("/v1/documents", status_code=202)
    def submit_document(document: DocumentRequest, request: Request, response: Response, identity: Identity, idempotency_key: Annotated[str, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]):
        task, replayed = store.enqueue(identity.tenant, idempotency_key, document.model_dump(), request.state.request_id)
        response.headers["Idempotency-Replayed"] = str(replayed).lower()
        response.headers["Location"] = f"/v1/tasks/{task['id']}"
        return task

    @app.get("/v1/tasks/{task_id}")
    def get_task(task_id: str, identity: Identity):
        return store.get_task(identity.tenant, task_id)

    @app.post("/v1/tasks/{task_id}/cancel")
    def cancel(task_id: str, identity: Identity):
        return store.cancel(identity.tenant, task_id)

    @app.get("/v1/tasks/{task_id}/events")
    def events(task_id: str, identity: Identity, after: int = Query(0, ge=0, le=MAX_CURSOR), limit: int = Query(100, ge=1, le=100)):
        items = store.task_events(identity.tenant, task_id, after, limit)
        return {"items": items, "next_cursor": items[-1]["id"] if items else after}

    @app.get("/v1/tasks/{task_id}/stream")
    async def stream(task_id: str, request: Request, identity: Identity, after: int = Query(0, ge=0, le=MAX_CURSOR), last_event_id: str | None = Header(default=None)):
        await asyncio.to_thread(store.get_task, identity.tenant, task_id)
        if last_event_id is not None:
            try:
                cursor = int(last_event_id)
                if not 0 <= cursor <= MAX_CURSOR:
                    raise ValueError
                after = max(after, cursor)
            except ValueError:
                raise HTTPException(400, "invalid Last-Event-ID")

        async def emit():
            cursor, deadline = after, time.monotonic() + settings.stream_seconds
            while time.monotonic() < deadline and not await request.is_disconnected():
                # Terminal state and its event commit together. Read state first,
                # then drain events; a later transition is handled next poll.
                task = await asyncio.to_thread(store.get_task, identity.tenant, task_id)
                items = await asyncio.to_thread(store.task_events, identity.tenant, task_id, cursor, 100)
                for item in items:
                    cursor = item["id"]
                    yield f"id: {cursor}\nevent: {item['kind']}\ndata: {json.dumps(item, ensure_ascii=True)}\n\n"
                if task["state"] in TERMINAL and len(items) < 100:
                    yield f"event: end\ndata: {json.dumps({'state': task['state']})}\n\n"
                    return
                if not items:
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(0.1)

        return StreamingResponse(emit(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/v1/documents/{document_id}")
    def get_document(document_id: str, identity: Identity):
        return store.get_document(identity.tenant, document_id)

    @app.get("/v1/search")
    def search(identity: Identity, q: str = Query(min_length=1, max_length=512), limit: int = Query(10, ge=1, le=20)):
        try:
            return {"items": store.search(identity.tenant, q, limit), "strategy": "fts5-lexical-and"}
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.get("/v1/stats")
    def stats(identity: Identity):
        return store.statistics(identity.tenant)

    @app.get("/v1/admin/stats")
    def admin_stats(identity: Identity):
        if identity.role != "admin":
            raise HTTPException(403, "admin role required")
        return store.statistics()

    return app
