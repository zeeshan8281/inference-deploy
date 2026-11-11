"""Single-host qualification, applied deployment state and verified recovery."""
from __future__ import annotations

import asyncio
import copy
import fcntl
import hashlib
import json
import os
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from .checks import evaluate, suite_data
from .runtime import (
    available_port,
    profile_data,
    stop_record,
    verify_artifacts,
    wait_ready,
)


def now():
    return datetime.now(UTC).isoformat()


def identity(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Manager:
    def __init__(self, state_dir: Path):
        self.directory = Path(state_dir).expanduser().resolve()
        self.state = None
        self.runtime = None
        self.process = None
        self.inflight = 0
        self.maintenance = True
        self.action = asyncio.Lock()
        self.condition = asyncio.Condition()
        self.lock_file = None
        self.started = False

    def _save(self):
        temporary = self.directory / f'.state-{uuid.uuid4().hex}.tmp'
        try:
            with temporary.open('x') as handle:
                json.dump(self.state, handle, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.directory / 'state.json')
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _phase(self, phase):
        if self.state['operation']:
            self.state['operation']['phase'] = phase
            self._save()

    async def start(self):
        if self.started:
            raise RuntimeError('manager is already started')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.directory / 'manager.lock').open('a+')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError('another manager owns this state directory') from exc
        try:
            target = self.directory / 'state.json'
            if target.is_symlink():
                raise ValueError('state file must not be a symbolic link')
            self.state = json.loads(target.read_text()) if target.exists() else {
                'schema': 1, 'instance': uuid.uuid4().hex, 'profiles': {}, 'suites': {},
                'active': None, 'previous': None, 'operation': None, 'history': [], 'managed': [],
            }
            if self.state.get('schema') != 1:
                raise ValueError('unsupported state schema')
            self._save()
            self.started = True
            for record in self.state['managed']:
                await stop_record(record)
            interrupted = self.state['operation']
            if interrupted:
                self.state['history'].append({**interrupted, 'status': 'interrupted', 'finished_at': now(),
                                              'error': 'manager restarted before operation completed'})
            self.state['operation'] = {'id': uuid.uuid4().hex, 'kind': 'recovery', 'phase': 'recovering', 'started_at': now()}
            self._save()
            try:
                await self._restore(self.state['active'])
                self.state['operation'] = None
                self._save()
            except Exception as exc:
                self.state['recovery_error'] = f'{type(exc).__name__}: {exc}'
                self.state['operation'] = None
                self._save()
                self.maintenance = True
        except BaseException:
            self.started = False
            self._unlock()
            raise

    def _unlock(self):
        if self.lock_file:
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None

    async def close(self):
        self.maintenance = True
        try:
            await self._stop_current()
        finally:
            self.started = False
            self._unlock()

    def status(self):
        if not self.state:
            return {'phase': 'starting', 'maintenance': True, 'inflight': self.inflight,
                    'active': None, 'previous': None, 'runtime': None, 'operation': None}
        alive = self.process is not None and self.process.poll() is None
        runtime = copy.deepcopy(self.runtime)
        if runtime:
            runtime['alive'] = alive
        active = self.state['active']
        previous = self.state['previous']
        return {
            'phase': self.state['operation']['phase'] if self.state['operation'] else ('ready' if alive and self.runtime.get('ready') else 'offline'),
            'maintenance': self.maintenance or not alive,
            'inflight': self.inflight, 'operation': copy.deepcopy(self.state['operation']),
            'active': copy.deepcopy(active), 'previous': copy.deepcopy(previous),
            'active_profile': copy.deepcopy(self.state['profiles'][active['profile_id']]) if active else None,
            'previous_profile': copy.deepcopy(self.state['profiles'][previous['profile_id']]) if previous else None,
            'runtime': runtime, 'recovery_error': self.state.get('recovery_error'),
        }

    def list_profiles(self):
        return copy.deepcopy(list(self.state['profiles'].values()))

    def create_profile(self, data):
        profile = profile_data(data)
        profile['id'] = identity(profile)
        self.state['profiles'].setdefault(profile['id'], profile)
        self._save()
        return copy.deepcopy(profile)

    def list_suites(self):
        return copy.deepcopy(list(self.state['suites'].values()))

    def create_suite(self, data):
        suite = suite_data(data)
        suite['id'] = identity(suite)
        self.state['suites'].setdefault(suite['id'], suite)
        self._save()
        return copy.deepcopy(suite)

    def history(self):
        return copy.deepcopy(self.state['history'])

    @asynccontextmanager
    async def lease(self):
        async with self.condition:
            if (self.maintenance or not self.runtime or not self.runtime['ready']
                    or self.process is None or self.process.poll() is not None):
                raise RuntimeError('endpoint is unavailable or in maintenance')
            self.inflight += 1
            route = {key: self.runtime[key] for key in ('url', 'alias', 'profile_id')}
        try:
            yield route
        finally:
            async with self.condition:
                self.inflight -= 1
                self.condition.notify_all()

    def _begin(self, kind, profile_id=None, suite_id=None):
        if not self.started:
            raise RuntimeError('manager is not started')
        run = {'id': uuid.uuid4().hex, 'kind': kind, 'phase': 'preflight', 'profile_id': profile_id,
               'suite_id': suite_id, 'started_at': now(), 'baseline': copy.deepcopy(self.state['active'])}
        self.state['operation'] = run
        self._save()
        return run

    def _finish(self, run, status, **evidence):
        result = {**run, **evidence, 'status': status, 'finished_at': now()}
        self.state['history'].append(result)
        self.state['operation'] = None
        self._save()
        return copy.deepcopy(result)

    async def _drain(self, timeout):
        async with self.condition:
            self.maintenance = True
            self._phase('draining')
            await asyncio.wait_for(self.condition.wait_for(lambda: self.inflight == 0), timeout)

    async def _stop_current(self):
        if self.runtime:
            await stop_record(self.runtime)
        if self.process:
            try:
                await asyncio.to_thread(self.process.wait, 1)
            except subprocess.TimeoutExpired:
                raise RuntimeError('owned runtime did not stop') from None
        self.runtime = None
        self.process = None

    async def _launch(self, profile, phase):
        await asyncio.to_thread(verify_artifacts, profile)
        port = available_port()
        marker = f"idp-{self.state['instance']}-{uuid.uuid4().hex}"
        slots = self.directory / 'slots' / marker
        slots.mkdir(parents=True, exist_ok=True)
        command = [profile['runtime'], '-m', profile['model'], *profile['args'], '--host', '127.0.0.1',
                   '--port', str(port), '--alias', marker, '--metrics', '--slots', '--no-webui',
                   '--slot-save-path', str(slots) + os.sep]
        if not any(flag in profile['args'] for flag in ('--parallel', '-np')):
            command.extend(['--parallel', '1'])
        log = self.directory / f'{marker}.log'
        record = {'profile_id': profile['id'], 'alias': marker, 'url': f'http://127.0.0.1:{port}',
                  'command': command, 'log': str(log), 'pid': None, 'ready': False}
        self.state['managed'].append(record)
        self._phase(phase)
        self._save()
        env = {key: value for key, value in os.environ.items() if not key.startswith('LLAMA_ARG_')}
        with log.open('ab', buffering=0) as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       env=env, start_new_session=True)
        self.process, self.runtime = process, record
        record['pid'] = process.pid
        self._save()
        record['readiness'] = await wait_ready(record, process, profile['startup_timeout'])
        record['ready'] = True
        self._save()
        return record

    async def _restore(self, active):
        if not active:
            self.maintenance = True
            return
        self._phase('restoring_previous')
        profile = self.state['profiles'][active['profile_id']]
        suite = self.state['suites'][active['suite_id']]
        try:
            route = await self._launch(profile, 'restoring_previous')
            check = await evaluate(route, suite)
            if not check['passed']:
                raise RuntimeError('previous profile failed full application recovery suite')
        except BaseException:
            await self._stop_current()
            raise
        self.maintenance = False
        self.state.pop('recovery_error', None)
        return check

    async def qualify(self, profile_id, suite_id):
        profile, suite = self.state['profiles'][profile_id], self.state['suites'][suite_id]
        if self.action.locked():
            raise RuntimeError('another deployment operation is in progress')
        async with self.action:
            run = self._begin('qualify', profile_id, suite_id)
            old = copy.deepcopy(self.state['active'])
            stopped = False
            result = None
            error = None
            restoration = None
            try:
                await asyncio.to_thread(verify_artifacts, profile)
                await self._drain(profile['drain_timeout'])
                self._phase('stopping_active')
                stopped = True
                await self._stop_current()
                route = await self._launch(profile, 'starting_candidate')
                run['candidate_runtime'] = copy.deepcopy(route)
                self._phase('qualifying')
                result = await evaluate(route, suite)
                await asyncio.to_thread(verify_artifacts, profile)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
            finally:
                if stopped:
                    try:
                        await self._stop_current()
                        restoration = await self._restore(old)
                    except Exception as exc:
                        error = (error + '; ' if error else '') + f'recovery failed: {type(exc).__name__}: {exc}'
                        self.maintenance = True
                else:
                    self.maintenance = not bool(self.runtime and self.runtime['ready'])
            status = 'passed' if result and result['passed'] and error is None else 'rejected'
            if result and result.get('inconclusive') and error is None:
                status = 'inconclusive'
            return self._finish(run, status, evaluation=result, error=error, restored=restoration)

    async def apply(self, profile_id, suite_id):
        profile, suite = self.state['profiles'][profile_id], self.state['suites'][suite_id]
        if self.action.locked():
            raise RuntimeError('another deployment operation is in progress')
        qualifying = next((row for row in reversed(self.state['history']) if row['kind'] == 'qualify'
                           and row['profile_id'] == profile_id and row['suite_id'] == suite_id), None)
        if qualifying is None or qualifying['status'] != 'passed':
            raise RuntimeError('successful qualification for this exact profile and suite is required')
        async with self.action:
            return await self._transition(profile, suite, 'apply', qualification=qualifying['id'])

    async def rollback(self):
        if self.action.locked():
            raise RuntimeError('another deployment operation is in progress')
        previous = copy.deepcopy(self.state['previous'])
        if not previous:
            raise RuntimeError('no previous approved profile is available')
        async with self.action:
            return await self._transition(self.state['profiles'][previous['profile_id']],
                                          self.state['suites'][previous['suite_id']], 'rollback')

    async def _transition(self, profile, suite, kind, **attributes):
        run = self._begin(kind, profile['id'], suite['id'])
        run.update(attributes)
        self._save()
        old = copy.deepcopy(self.state['active'])
        prior_previous = copy.deepcopy(self.state['previous'])
        stopped = False
        try:
            await asyncio.to_thread(verify_artifacts, profile)
            await self._drain(profile['drain_timeout'])
            self._phase('stopping_active')
            stopped = True
            await self._stop_current()
            route = await self._launch(profile, 'starting_candidate')
            run['candidate_runtime'] = copy.deepcopy(route)
            self._phase('postcheck')
            evaluation = await evaluate(route, suite)
            if not evaluation['passed']:
                run['evaluation'] = evaluation
                raise RuntimeError('candidate failed post-apply application checks')
            await asyncio.to_thread(verify_artifacts, profile)
            self._phase('committing')
            self.state['previous'] = old
            self.state['active'] = {'profile_id': profile['id'], 'suite_id': suite['id']}
            self.state.pop('recovery_error', None)
            self.maintenance = False
            return self._finish(run, 'applied' if kind == 'apply' else 'rolled_back', evaluation=evaluation)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            self.state['active'] = old
            self.state['previous'] = prior_previous
            if not stopped:
                self.maintenance = not bool(self.runtime and self.runtime['ready'])
                return self._finish(run, 'rejected', error=error)
            try:
                await self._stop_current()
                restored = await self._restore(old)
                return self._finish(run, 'rolled_back' if old else 'rejected', error=error, restored=restored)
            except Exception as recovery:
                self.maintenance = True
                self.state['recovery_error'] = f'{type(recovery).__name__}: {recovery}'
                return self._finish(run, 'recovery_failed', error=error, recovery_error=self.state['recovery_error'])
