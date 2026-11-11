"""Run the local deployment manager or operate its management API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inference-deploy", description=__doc__)
    parser.add_argument(
        "--url", default="http://127.0.0.1:8088", help="manager base URL"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="start the dashboard, gateway and runtime supervisor"
    )
    serve.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local/share/inference-deploy"
    )
    serve.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    for name in ("status", "profiles", "suites", "history", "rollback"):
        commands.add_parser(name)
    for name in ("profile", "suite"):
        sub = commands.add_parser(name, help=f"register an immutable {name} from JSON")
        sub.add_argument("file", type=Path)
    for name in ("qualify", "apply"):
        sub = commands.add_parser(name)
        sub.add_argument("profile_id")
        sub.add_argument("suite_id")
    args = parser.parse_args(argv)
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        import uvicorn

        from inference_deploy.server import create_app

        uvicorn.run(
            create_app(args.state_dir.resolve()),
            host=args.host,
            port=args.port,
            timeout_graceful_shutdown=30,
        )
        return 0
    path, method, data = f"/api/{args.command}", "GET", None
    try:
        if args.command in ("profile", "suite"):
            path = f"/api/{args.command}s"
            method, data = "POST", json.loads(args.file.read_text())
        elif args.command in ("qualify", "apply"):
            method, data = (
                "POST",
                {"profile_id": args.profile_id, "suite_id": args.suite_id},
            )
        elif args.command == "rollback":
            method, data = "POST", {}
        response = httpx.request(
            method,
            args.url.rstrip("/") + path,
            json=data,
            timeout=httpx.Timeout(3600, connect=10),
        )
        try:
            result = response.json()
        except ValueError:
            result = {"detail": response.text}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if response.is_success else 1
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"inference-deploy: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
