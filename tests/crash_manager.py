"""Test-only SIGKILL injection after a real manager journal write."""

import argparse
import os
import signal
from pathlib import Path

import uvicorn

from inference_deploy.manager import Manager
from inference_deploy.server import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--phase", required=True)
args = parser.parse_args()

original_save = Manager._save


def crash_after_journal(self):
    original_save(self)
    operation = self.state.get("operation")
    hit = (
        operation
        and operation.get("kind") == "apply"
        and operation.get("phase") == args.phase
    )
    if args.phase == "after_spawn":
        hit = (
            operation
            and operation.get("kind") == "apply"
            and self.runtime
            and self.runtime.get("pid")
            and not self.runtime.get("ready")
        )
    elif args.phase == "after_commit":
        history = self.state.get("history", [])
        hit = (
            not operation
            and history
            and history[-1].get("kind") == "apply"
            and history[-1].get("status") == "applied"
        )
    if hit:
        os.kill(os.getpid(), signal.SIGKILL)


Manager._save = crash_after_journal
uvicorn.run(
    create_app(args.state), host="127.0.0.1", port=args.port, log_level="warning"
)
