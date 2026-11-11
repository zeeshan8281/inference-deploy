"""Real llama-server upgrade/rollback exercise. Requires an actual local GGUF."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument(
        "--runtime", type=Path, default=Path("/opt/homebrew/bin/llama-server")
    )
    parser.add_argument("--port", type=int, default=18910)
    parser.add_argument("--output", type=Path, default=Path("evidence/live-flow"))
    args = parser.parse_args()
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    state = destination / "state"
    base = f"http://127.0.0.1:{args.port}"
    records = []
    manager = None
    log = None

    def record(name, value):
        records.append({"name": name, "time": time.time(), "evidence": value})
        (destination / "results.json").write_text(json.dumps(records, indent=2) + "\n")
        print(name, flush=True)

    def request(method, path, data=None, expected=200):
        response = httpx.request(method, base + path, json=data, timeout=180)
        if response.status_code != expected:
            raise AssertionError(
                f"{method} {path}: expected {expected}, got {response.status_code}: {response.text[:1500]}"
            )
        return response.json(), response.headers

    def status():
        return request("GET", "/api/status")[0]

    def start():
        nonlocal manager, log
        log = (
            destination / f"manager-{len(list(destination.glob('manager-*.log')))}.log"
        ).open("w")
        manager = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "inference_deploy.cli",
                "serve",
                "--state-dir",
                str(state),
                "--port",
                str(args.port),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if manager.poll() is not None:
                raise RuntimeError(f"manager exited: {log.name}")
            try:
                if httpx.get(base + "/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise TimeoutError("manager did not become ready")

    def stop(force=False):
        nonlocal manager, log
        if manager and manager.poll() is None:
            manager.send_signal(signal.SIGKILL if force else signal.SIGTERM)
            manager.wait(timeout=45)
        if log:
            log.close()

    def chat():
        body, headers = request(
            "POST",
            "/v1/chat/completions",
            {
                "model": "application",
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly READY and nothing else.",
                    }
                ],
                "temperature": 0,
                "max_tokens": 16,
            },
        )
        assert body["choices"][0]["message"]["content"].strip() == "READY", body
        return {
            "profile": headers.get("x-inference-profile"),
            "model": body.get("model"),
            "status": status(),
        }

    def create_profile(name, context):
        body = {
            "name": name,
            "runtime": str(args.runtime.resolve()),
            "model": str(args.model.resolve()),
            "args": [
                "--ctx-size",
                str(context),
                "--n-gpu-layers",
                "99",
                "--parallel",
                "1",
            ],
            "startup_timeout": 30,
            "drain_timeout": 15,
        }
        return request("POST", "/api/profiles", body, expected=201)[0]

    def action(name, profile, suite):
        return request(
            "POST",
            f"/api/{name}",
            {"profile_id": profile["id"], "suite_id": suite["id"]},
        )[0]

    try:
        start()
        record("fresh_manager", status())
        request("GET", "/v1/models", expected=503)
        stable = create_profile("Stable · 2048 context", 2048)
        candidate = create_profile("Candidate · 3072 context", 3072)
        small = create_profile("Regression · 128 context", 128)
        suite = request(
            "POST",
            "/api/suites",
            {
                "name": "Application acceptance",
                "cases": [
                    {
                        "name": "ready",
                        "messages": [
                            {
                                "role": "user",
                                "content": "Reply with exactly READY and nothing else.",
                            }
                        ],
                        "expect": {"equals": "READY"},
                        "max_tokens": 16,
                        "max_latency_ms": 30000,
                    },
                    {
                        "name": "json",
                        "messages": [
                            {
                                "role": "user",
                                "content": 'Return only this JSON object: {"status":"ready"}',
                            }
                        ],
                        "expect": {
                            "json_schema": {
                                "type": "object",
                                "properties": {"status": {"const": "ready"}},
                                "required": ["status"],
                                "additionalProperties": False,
                            }
                        },
                        "max_tokens": 32,
                        "max_latency_ms": 30000,
                    },
                    {
                        "name": "context",
                        "messages": [
                            {
                                "role": "user",
                                "content": "Ignore the following repeated filler: "
                                + "word " * 400
                                + "\nReply with exactly READY and nothing else.",
                            }
                        ],
                        "expect": {"equals": "READY"},
                        "max_tokens": 16,
                        "max_latency_ms": 30000,
                    },
                ],
            },
            expected=201,
        )[0]
        record(
            "registered_profiles_and_suite",
            {"stable": stable, "candidate": candidate, "small": small, "suite": suite},
        )
        request(
            "POST",
            "/api/apply",
            {"profile_id": stable["id"], "suite_id": suite["id"]},
            expected=409,
        )
        q = action("qualify", stable, suite)
        assert q["status"] == "passed", q
        record("stable_qualified", q)
        applied = action("apply", stable, suite)
        assert applied["status"] == "applied", applied
        active = chat()
        assert active["profile"] == stable["id"], active
        record("bootstrap_serves_real_inference", {"run": applied, "request": active})

        q = action("qualify", small, suite)
        assert q["status"] == "rejected", q
        assert chat()["profile"] == stable["id"]
        record("context_regression_rejected_and_stable_restored", q)
        request(
            "POST",
            "/api/apply",
            {"profile_id": small["id"], "suite_id": suite["id"]},
            expected=409,
        )

        q = action("qualify", candidate, suite)
        assert q["status"] == "passed", q
        assert chat()["profile"] == stable["id"]
        record("candidate_qualified_stable_restored", q)

        with concurrent.futures.ThreadPoolExecutor() as pool:
            with httpx.Client(timeout=180) as client:
                with client.stream(
                    "POST",
                    base + "/v1/chat/completions",
                    json={
                        "model": "application",
                        "messages": [
                            {
                                "role": "user",
                                "content": "Write an extremely long story of at least 1000 words about an ocean voyage.",
                            }
                        ],
                        "max_tokens": 1024,
                        "temperature": 0,
                        "stream": True,
                    },
                ) as stream:
                    assert stream.status_code == 200
                    for line in stream.iter_lines():
                        if line.startswith("data:") and '"content"' in line:
                            break
                    assert stream.headers["x-inference-profile"] == stable["id"]
                    future = pool.submit(action, "apply", candidate, suite)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        current = status()
                        if current.get("maintenance"):
                            break
                        time.sleep(0.01)
                    else:
                        raise AssertionError("apply did not enter maintenance")
                    request("GET", "/v1/models", expected=503)
                    assert not future.done(), "apply failed to wait for active stream"
                    record("apply_drains_old_stream_and_rejects_new_requests", current)
                # Closing the real client stream propagates upstream cancellation.
                applied = future.result(timeout=120)
                assert applied["status"] == "applied", applied
        assert chat()["profile"] == candidate["id"]
        record("upgrade_routes_new_requests_to_candidate", applied)

        rolled = request("POST", "/api/rollback", {})[0]
        assert rolled["status"] == "rolled_back", rolled
        assert chat()["profile"] == stable["id"]
        record("manual_rollback_restores_real_service", rolled)

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(action, "apply", candidate, suite)
            killed = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not future.done():
                current = status()
                runtime = current.get("runtime") or {}
                if (
                    current.get("phase") == "starting_candidate"
                    and runtime.get("profile_id") == candidate["id"]
                    and runtime.get("pid")
                ):
                    os.kill(runtime["pid"], signal.SIGKILL)
                    killed = True
                    record("injected_real_candidate_startup_failure", current)
                    break
                time.sleep(0.005)
            assert killed, (
                "did not catch candidate startup for external process termination"
            )
            result = future.result(timeout=120)
            assert result["status"] == "rolled_back", result
        assert chat()["profile"] == stable["id"]
        record("failed_upgrade_automatically_rolls_back", result)

        record("pre_restart_state", status())
        stop(force=True)
        start()
        assert chat()["profile"] == stable["id"]
        record("manager_crash_recovers_committed_profile", status())

        history = request("GET", "/api/history")[0]
        record("retained_history", history)
        (destination / "summary.json").write_text(
            json.dumps(
                {
                    "complete": True,
                    "model": str(args.model.resolve()),
                    "runtime": str(args.runtime.resolve()),
                    "checks": [row["name"] for row in records],
                    "manager_url": base,
                    "scope": "Real local llama-server, GGUF model, streaming HTTP, actual process failure and manager restart.",
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Full real-backend flow passed. Evidence {destination}", flush=True)
    except BaseException as exc:
        record("failure", {"type": type(exc).__name__, "detail": str(exc)})
        raise
    finally:
        stop()


if __name__ == "__main__":
    main()
