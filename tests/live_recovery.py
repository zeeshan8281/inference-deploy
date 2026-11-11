"""Crash each real upgrade phase and verify actual endpoint recovery."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state", type=Path, required=True, help="state from a completed live_flow run"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18911)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = f"http://127.0.0.1:{args.port}"
    saved = json.loads((args.state / "state.json").read_text())
    stable = next(
        p for p in saved["profiles"].values() if p["name"].startswith("Stable")
    )
    candidate = next(
        p for p in saved["profiles"].values() if p["name"].startswith("Candidate")
    )
    suite = next(iter(saved["suites"]))
    outcomes = []

    def launch(phase=None):
        log = (
            args.output / f"{len(outcomes)}-{'fault' if phase else 'recovery'}.log"
        ).open("w")
        command = (
            [
                sys.executable,
                str(Path(__file__).with_name("crash_manager.py")),
                "--state",
                str(args.state),
                "--port",
                str(args.port),
                "--phase",
                phase,
            ]
            if phase
            else [
                sys.executable,
                "-m",
                "inference_deploy.cli",
                "serve",
                "--state-dir",
                str(args.state),
                "--port",
                str(args.port),
            ]
        )
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"manager failed to start: {log.name}")
            try:
                if httpx.get(base + "/healthz", timeout=1).is_success:
                    return process, log
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise TimeoutError("manager startup")

    def stop(process, log):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=45)
        log.close()

    phases = [
        "preflight",
        "draining",
        "stopping_active",
        "starting_candidate",
        "after_spawn",
        "postcheck",
        "committing",
        "after_commit",
    ]
    for phase in phases:
        process, log = launch(phase)
        try:
            try:
                response = httpx.post(
                    base + "/api/apply",
                    json={"profile_id": candidate["id"], "suite_id": suite},
                    timeout=120,
                )
                raise AssertionError(
                    f"fault did not kill manager: {response.status_code} {response.text[:500]}"
                )
            except httpx.HTTPError:
                pass
            assert process.wait(timeout=20) == -9
        finally:
            stop(process, log)
        process, log = launch()
        try:
            status = httpx.get(base + "/api/status", timeout=10).json()
            expected = candidate["id"] if phase == "after_commit" else stable["id"]
            assert status["active_profile"]["id"] == expected, status
            assert status["phase"] == "ready" and not status["maintenance"]
            response = httpx.post(
                base + "/v1/chat/completions",
                json={
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
                timeout=60,
            )
            response.raise_for_status()
            assert response.headers["x-inference-profile"] == expected
            assert (
                response.json()["choices"][0]["message"]["content"].strip() == "READY"
            )
            outcomes.append(
                {
                    "phase": phase,
                    "expected_profile_id": expected,
                    "actual_profile_id": status["active_profile"]["id"],
                    "phase_after_restart": status["phase"],
                    "runtime_pid": status["runtime"]["pid"],
                    "inference": response.json(),
                    "passed": True,
                }
            )
            (args.output / "results.json").write_text(
                json.dumps(outcomes, indent=2) + "\n"
            )
            print(
                f"{phase}: restarted with intended profile and successful real inference",
                flush=True,
            )
            if phase == "after_commit":
                rollback = httpx.post(base + "/api/rollback", json={}, timeout=180)
                rollback.raise_for_status()
                assert rollback.json()["status"] == "rolled_back"
        finally:
            stop(process, log)
    print("All persisted upgrade crash boundaries recovered.", flush=True)


if __name__ == "__main__":
    main()
