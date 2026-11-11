"""Gateway HTTP tests against a labelled TCP fixture, separate from real-model checks."""
from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn

from inference_deploy.server import create_app


class FixtureManager:
    backend = ""
    instance = None

    def __init__(self, state_dir):
        type(self).instance = self
        self.inflight = 0
        self.maintenance = False
        self.operation_started = asyncio.Event()
        self.operation_continue = asyncio.Event()
        self.operation_completed = asyncio.Event()

    async def start(self):
        pass

    async def close(self):
        pass

    def status(self):
        return {"inflight": self.inflight, "maintenance": self.maintenance}

    def list_profiles(self):
        return []

    def create_profile(self, body):
        if body.get("name") == "invalid":
            raise ValueError("invalid profile")
        return {"id": "fixture-profile", **body}

    def list_suites(self):
        return []

    def create_suite(self, body):
        return {"id": "fixture-suite", **body}

    def history(self):
        return []

    async def qualify(self, profile, suite):
        if profile == "missing":
            raise KeyError(profile)
        if profile == "conflict":
            raise RuntimeError("operation in progress")
        self.operation_started.set()
        await self.operation_continue.wait()
        self.operation_completed.set()
        return {"status": "pass"}

    async def apply(self, profile, suite):
        return {"status": "pass"}

    async def rollback(self):
        return {"status": "pass"}

    @asynccontextmanager
    async def lease(self):
        if self.maintenance:
            raise RuntimeError("maintenance")
        self.inflight += 1
        try:
            yield {"url": self.backend, "profile_id": "fixture-profile", "alias": "owned-backend-alias"}
        finally:
            self.inflight -= 1


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.disconnect_seen = asyncio.Event()
        self.upstream_tasks = set()
        self.upstream = await asyncio.start_server(self.backend, "127.0.0.1", 0)
        port = self.upstream.sockets[0].getsockname()[1]
        FixtureManager.backend = f"http://127.0.0.1:{port}"
        self.patch = patch("inference_deploy.server.Manager", FixtureManager)
        self.patch.start()
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.directory.name))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.gateway_port = sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="critical", lifespan="on"))
        self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
        for _ in range(100):
            if self.server.started:
                break
            await asyncio.sleep(.01)
        self.assertTrue(self.server.started)
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.gateway_port}", trust_env=False)
        self.manager = FixtureManager.instance

    async def asyncTearDown(self):
        self.manager.operation_continue.set()
        await self.client.aclose()
        self.server.should_exit = True
        await asyncio.wait_for(self.server_task, 5)
        self.upstream.close()
        await self.upstream.wait_closed()
        for task in list(self.upstream_tasks):
            task.cancel()
        await asyncio.gather(*self.upstream_tasks, return_exceptions=True)
        self.directory.cleanup()
        self.patch.stop()

    async def backend(self, reader, writer):
        task = asyncio.current_task()
        self.upstream_tasks.add(task)
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            headers = raw.decode().split("\r\n")
            length = next((int(x.split(":", 1)[1]) for x in headers if x.lower().startswith("content-length:")), 0)
            body = json.loads(await reader.readexactly(length)) if length else None
            self.requests.append({"request": headers[0], "body": body})
            if body and body.get("fixture_error"):
                writer.write(b"HTTP/1.1 422 Unprocessable Entity\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"error\":\"fixture rejected\"}")
                await writer.drain()
            elif body and body.get("stream"):
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
                count = 100 if body.get("fixture_long") else 3
                for i in range(count):
                    writer.write(f'data: {{"chunk":{i}}}\n\n'.encode())
                    await writer.drain()
                    if body.get("fixture_long"):
                        try:
                            result = await asyncio.wait_for(reader.read(1), .02)
                            if result == b"":
                                self.disconnect_seen.set()
                                break
                        except TimeoutError:
                            pass
                else:
                    writer.write(b"data: [DONE]\n\n")
                    await writer.drain()
            else:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"ok\":true}")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            self.disconnect_seen.set()
        finally:
            writer.close()
            await writer.wait_closed()
            self.upstream_tasks.discard(task)

    def payload(self, **extra):
        return {"model": "caller-model", "messages": [{"role": "user", "content": "fixture"}], **extra}

    async def test_proxy_model_identity_nonstream_and_errors(self):
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(response.headers["x-inference-profile"], "fixture-profile")
        self.assertEqual(self.requests[-1]["body"]["model"], "owned-backend-alias")
        self.assertEqual(self.manager.inflight, 0)
        response = await self.client.post("/v1/chat/completions", json=self.payload(fixture_error=True))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"error": "fixture rejected"})
        self.assertEqual(self.manager.inflight, 0)

    async def test_sse_bytes_preserved_and_disconnect_releases_upstream(self):
        response = await self.client.post("/v1/chat/completions", json=self.payload(stream=True))
        self.assertEqual(response.content, b'data: {"chunk":0}\n\ndata: {"chunk":1}\n\ndata: {"chunk":2}\n\ndata: [DONE]\n\n')
        async with self.client.stream("POST", "/v1/chat/completions", json=self.payload(stream=True, fixture_long=True)) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    break
        await asyncio.wait_for(self.disconnect_seen.wait(), 2)
        for _ in range(100):
            if self.manager.inflight == 0:
                break
            await asyncio.sleep(.01)
        self.assertEqual(self.manager.inflight, 0)
        self.assertTrue((await self.client.post("/v1/chat/completions", json=self.payload())).json()["ok"])

    async def test_backend_connection_failure_releases_lease(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.manager.backend = f"http://127.0.0.1:{probe.getsockname()[1]}"
        probe.close()
        with self.assertLogs("inference_deploy.server", level="WARNING"):
            response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.manager.inflight, 0)

    async def test_maintenance_rejects_without_contacting_backend(self):
        self.manager.maintenance = True
        response = await self.client.post("/v1/chat/completions", json=self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertEqual(self.requests, [])

    async def test_control_validation_origin_and_bounded_body(self):
        response = await self.client.post("/api/profiles", json={"name": "invalid"})
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/api/profiles", json={"name": "good"}, headers={"Origin": "https://external.invalid"})
        self.assertEqual(response.status_code, 403)
        for profile, status in (("missing", 404), ("conflict", 409)):
            response = await self.client.post("/api/qualify", json={"profile_id": profile, "suite_id": "suite"})
            self.assertEqual(response.status_code, status)
        response = await self.client.post("/v1/chat/completions", content=b"x" * (2 * 1024 * 1024 + 1), headers={"content-type": "application/json"})
        self.assertEqual(response.status_code, 413)
        response = await self.client.post("/api/qualify", json={"profile_id": []})
        self.assertEqual(response.status_code, 400)

    async def test_client_departure_does_not_cancel_qualification(self):
        task = asyncio.create_task(self.client.post("/api/qualify", json={"profile_id": "good", "suite_id": "suite"}))
        await asyncio.wait_for(self.manager.operation_started.wait(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.manager.operation_continue.set()
        await asyncio.wait_for(self.manager.operation_completed.wait(), 2)
        self.assertEqual((await self.client.get("/healthz")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
