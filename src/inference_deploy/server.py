"""Local operator API and a lease-pinned, cancellation-aware inference gateway."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .manager import Manager

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
FORWARDED_HEADERS = {"content-type", "cache-control", "content-encoding", "retry-after"}


async def json_body(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise HTTPException(415, "Content-Type must be application/json")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
        raise HTTPException(413, "Request exceeds 2 MiB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Request exceeds 2 MiB")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Invalid JSON body") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "Expected a JSON object")
    return value


def operation_ids(body: dict) -> tuple[str, str]:
    values = (body.get("profile_id"), body.get("suite_id"))
    if not all(isinstance(value, str) and value for value in values):
        raise HTTPException(400, "profile_id and suite_id are required")
    return values


class LeasedStream(StreamingResponse):
    """Release upstream and deployment lease even if sending headers is cancelled."""

    def __init__(self, upstream, release, headers):
        self.upstream = upstream
        self.release = release
        super().__init__(self.chunks(), status_code=upstream.status_code, headers=headers)

    async def chunks(self):
        try:
            async for chunk in self.upstream.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            # Headers may already have reached the client. End the failed stream.
            LOGGER.warning("Upstream stream interrupted", exc_info=True)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.release()


def create_app(state_dir: Path) -> FastAPI:
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app):
        manager = Manager(Path(state_dir))
        app.state.manager = manager
        await manager.start()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(300, connect=10, write=30, pool=10),
                limits=httpx.Limits(max_connections=100),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                app.state.client = client
                try:
                    yield
                finally:
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await manager.close()

    app = FastAPI(title="Inference Deploy", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "::1", "[::1]"])

    @app.middleware("http")
    async def control_origin(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    same_origin = parsed.scheme in {"http", "https"} and parsed.netloc == request.headers.get("host")
                except ValueError:
                    same_origin = False
                if not same_origin:
                    return JSONResponse({"detail": "Cross-origin management requests are forbidden"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing_resource(request, exc):
        return JSONResponse({"detail": "Profile or suite not found"}, status_code=404)

    @app.exception_handler(RuntimeError)
    async def conflicting_operation(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    async def run_operation(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.add(task)

        def finished(done):
            tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        # A browser disappearing must not interrupt an applied runtime transition.
        return await asyncio.shield(task)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def status():
        return app.state.manager.status()

    @app.get("/api/profiles")
    async def profiles():
        return app.state.manager.list_profiles()

    @app.post("/api/profiles", status_code=201)
    async def create_profile(request: Request):
        return app.state.manager.create_profile(await json_body(request))

    @app.get("/api/suites")
    async def suites():
        return app.state.manager.list_suites()

    @app.post("/api/suites", status_code=201)
    async def create_suite(request: Request):
        return app.state.manager.create_suite(await json_body(request))

    @app.get("/api/history")
    async def history():
        return app.state.manager.history()

    @app.post("/api/qualify")
    async def qualify(request: Request):
        profile_id, suite_id = operation_ids(await json_body(request))
        return await run_operation(app.state.manager.qualify(profile_id, suite_id))

    @app.post("/api/apply")
    async def apply(request: Request):
        profile_id, suite_id = operation_ids(await json_body(request))
        return await run_operation(app.state.manager.apply(profile_id, suite_id))

    @app.post("/api/rollback")
    async def rollback():
        return await run_operation(app.state.manager.rollback())

    async def proxy(request: Request, path: str, body: dict | None):
        lease = app.state.manager.lease()
        try:
            route = await lease.__aenter__()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": "1"}) from exc
        upstream = None
        released = False
        handed_off = False

        async def release():
            nonlocal released
            if not released:
                released = True
                try:
                    if upstream is not None:
                        await upstream.aclose()
                finally:
                    await lease.__aexit__(None, None, None)

        try:
            if body is not None:
                body["model"] = route["alias"]
            outgoing = app.state.client.build_request(
                request.method,
                route["url"].rstrip("/") + path,
                json=body,
                headers={"Accept-Encoding": "identity", "Accept": request.headers.get("accept", "*/*")},
            )
            upstream = await app.state.client.send(outgoing, stream=True)
            headers = {key: value for key, value in upstream.headers.items() if key.lower() in FORWARDED_HEADERS}
            headers["X-Inference-Profile"] = route["profile_id"]
            if body is not None and body.get("stream") is True and upstream.status_code < 400:
                response = LeasedStream(upstream, release, headers)
                handed_off = True
                return response
            content = bytearray()
            async for chunk in upstream.aiter_raw():
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise HTTPException(502, "Upstream response exceeds gateway limit")
            return Response(bytes(content), status_code=upstream.status_code, headers=headers)
        except httpx.HTTPError as exc:
            LOGGER.warning("Inference backend unavailable", exc_info=True)
            raise HTTPException(502, "Inference backend unavailable") from exc
        finally:
            # StreamingResponse owns cleanup only after a successful return.
            if not handed_off:
                with anyio.CancelScope(shield=True):
                    await release()

    @app.get("/v1/models")
    async def models(request: Request):
        return await proxy(request, "/v1/models", None)

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await json_body(request)
        if "stream" in body and not isinstance(body["stream"], bool):
            raise HTTPException(400, "stream must be a boolean")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise HTTPException(400, "messages must be a nonempty list")
        return await proxy(request, "/v1/chat/completions", body)

    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    return app
