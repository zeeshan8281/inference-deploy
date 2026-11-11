"""Owned llama-server processes and immutable launch identities."""
from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx

VALUE_FLAGS = {
    '--ctx-size', '-c', '--n-gpu-layers', '--gpu-layers', '-ngl', '--parallel', '-np',
    '--batch-size', '-b', '--ubatch-size', '-ub', '--threads', '-t', '--threads-batch', '-tb',
    '--seed', '--cache-type-k', '--cache-type-v', '--flash-attn', '--chat-template',
}


def digest_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def verify_native_runtime(path: Path) -> None:
    # Native argv identity is the ownership contract used for process cleanup.
    native_headers = {
        b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
    }
    with path.open("rb") as handle:
        if handle.read(4) not in native_headers:
            raise ValueError("runtime must be a native llama-server binary; script wrappers are unsupported")


def profile_data(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get('name'), str) or not data['name'].strip():
        raise ValueError('profile name is required')
    if set(data) - {'name', 'runtime', 'model', 'args', 'startup_timeout', 'drain_timeout'}:
        raise ValueError('unknown profile field')
    paths = {}
    for field in ('runtime', 'model'):
        raw = data.get(field)
        if (not isinstance(raw, str) or any(char in raw for char in ('\n', '\r', '\0'))
                or not Path(raw).is_absolute()):
            raise ValueError(f'{field} must be an absolute file path')
        try:
            path = Path(raw).resolve(strict=True)
        except OSError as exc:
            raise ValueError(f'{field} file is unavailable') from exc
        if not path.is_file() or (field == 'runtime' and not os.access(path, os.X_OK)):
            raise ValueError(f'{field} is not an accessible file')
        if field == 'runtime':
            verify_native_runtime(path)
        paths[field] = str(path)
        paths[field + '_sha256'] = digest_file(path)
    args = data.get('args', [])
    if not isinstance(args, list) or len(args) % 2 or len(args) > 40:
        raise ValueError('args must be supported flag/value pairs')
    for flag, value in zip(args[::2], args[1::2], strict=True):
        if not isinstance(flag, str) or flag not in VALUE_FLAGS or not isinstance(value, str) or not value or any(char in value for char in ('\n', '\r', '\0')):
            raise ValueError(f'unsupported runtime argument {flag!r}')
        if flag in ('--parallel', '-np') and value != '1':
            raise ValueError('this release requires one llama-server slot')
    result = {'name': data['name'].strip()[:100], **paths, 'args': args}
    for field, default, maximum in (('startup_timeout', 60, 300), ('drain_timeout', 15, 120)):
        value = data.get(field, default)
        if type(value) not in (int, float) or not 0 < value <= maximum:
            raise ValueError(f'{field} must be positive and at most {maximum}')
        result[field] = value
    return result


def verify_artifacts(profile: dict) -> None:
    verify_native_runtime(Path(profile['runtime']))
    for field in ('runtime', 'model'):
        if digest_file(Path(profile[field])) != profile[field + '_sha256']:
            raise ValueError(f'{field} artifact changed since profile creation')


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def process_matches(pid: int, command: list[str]) -> bool:
    try:
        output = subprocess.check_output(['ps', '-ww', '-p', str(pid), '-o', 'pgid=', '-o', 'command='], text=True)
        group, rendered = output.strip().split(None, 1)
        return int(group) == pid and rendered == " ".join(command)
    except (ValueError, OSError, subprocess.CalledProcessError):
        return False


def matching_processes(command: list[str]) -> list[int]:
    output = subprocess.check_output(['ps', '-ww', '-axo', 'pid=,pgid=,command='], text=True)
    found = []
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid, group = map(int, fields[:2])
            if pid == group and fields[2] == " ".join(command):
                found.append(pid)
        except ValueError:
            continue
    return found


async def stop_record(record: dict) -> None:
    """Only terminate the exact independently grouped command we journaled."""
    command = record['command']
    pids = matching_processes(command)
    for pid in pids:
        if process_matches(pid, command):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(process_matches(pid, command) for pid in pids):
        await asyncio.sleep(.05)
    for pid in pids:
        if process_matches(pid, command):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


async def wait_ready(record: dict, process: subprocess.Popen, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last = 'server not ready'
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'candidate process exited with code {process.returncode}')
            try:
                health = await client.get(record['url'] + '/health')
                health.raise_for_status()
                models = (await client.get(record['url'] + '/v1/models')).json()
                if record['alias'] not in [row.get('id') for row in models.get('data', [])]:
                    raise RuntimeError('runtime readiness identity mismatch')
                props = await client.get(record['url'] + '/props')
                return {'health': health.json(), 'models': models,
                        'props': props.json() if props.is_success else None}
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last = str(exc)
            await asyncio.sleep(.1)
    raise RuntimeError(f'startup deadline exceeded: {last}')
