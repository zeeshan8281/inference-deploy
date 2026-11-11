"""Focused state/contract checks. Synthetic evaluations are not model validation."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from inference_deploy.checks import acceptance, evaluate, suite_data
from inference_deploy.manager import Manager
from inference_deploy.runtime import (
    matching_processes,
    process_matches,
    profile_data,
    stop_record,
)


class ContractChecks(unittest.TestCase):
    def test_application_checks_reject_well_formed_wrong_answers(self):
        self.assertFalse(acceptance('{"city":"Paris"}', {
            'contains': 'Paris', 'json_schema': {'type': 'object', 'required': ['city'],
                                               'properties': {'city': {'const': 'Paris'}}},
        }))
        self.assertTrue(acceptance('{"city":"London"}', {
            'json_schema': {'type': 'object', 'properties': {'city': {'const': 'Paris'}}},
        }))
        self.assertTrue(acceptance(' READY extra ', {'equals': 'READY'}))

    def test_suite_rejects_external_refs_and_invalid_schema(self):
        base = {'name': 'test', 'cases': [{'name': 'x', 'messages': [{'role': 'user', 'content': 'hi'}],
                                         'expect': {'contains': 'hi'}}]}
        for schema in ({'$ref': 'https://example.com/schema'}, {'type': 'unknown'}):
            base['cases'][0]['expect'] = {'json_schema': schema}
            with self.assertRaises(ValueError):
                suite_data(base)

    def test_script_runtime_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "wrapper"
            script.write_text("#!/bin/sh\nsleep 30\n")
            script.chmod(0o755)
            model = Path(folder) / "model.gguf"
            model.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "native llama-server binary"):
                profile_data({"name": "script", "runtime": str(script), "model": str(model)})
            native = profile_data({"name": "native", "runtime": sys.executable, "model": str(model)})
            self.assertTrue(native["runtime_sha256"])

    def test_profiles_reject_runtime_control_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'test model.gguf'
            model.write_bytes(b'fixture')
            for args in (['--host', '0.0.0.0'], ['-m', str(model)], ['--alias', 'fake'], ['--ctx-size', '2\n--host']):
                with self.assertRaises(ValueError):
                    profile_data({'name': 'test', 'runtime': sys.executable, 'model': str(model), 'args': args})


class OwnershipChecks(unittest.IsolatedAsyncioTestCase):
    async def test_exact_argv_with_spaces_and_group_owned_termination(self):
        command = [sys.executable, '-c', 'import time; time.sleep(30)']
        process = subprocess.Popen(command, start_new_session=True)
        try:
            self.assertTrue(process_matches(process.pid, command))
            self.assertIn(process.pid, matching_processes(command))
            self.assertFalse(process_matches(process.pid, command + ['different']))
            await stop_record({'command': command, 'pid': process.pid})
            await asyncio.to_thread(process.wait, 2)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    async def test_owned_runtime_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="runtime path ") as folder:
            runtime = Path(folder) / "sleep executable"
            runtime.symlink_to("/bin/sleep")
            command = [str(runtime), "30"]
            process = subprocess.Popen(command, start_new_session=True)
            try:
                self.assertTrue(process_matches(process.pid, command))
                await stop_record({"command": command, "pid": process.pid})
                await asyncio.to_thread(process.wait, 2)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    async def test_restart_cleans_journal_gap_process_and_retains_interruption(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = Manager(Path(folder))
            await manager.start()
            command = [sys.executable, '-c', 'import time; time.sleep(30)']
            process = subprocess.Popen(command, start_new_session=True)
            try:
                manager.state['managed'].append({'command': command, 'pid': None})
                manager.state['operation'] = {'id': 'interrupted', 'kind': 'apply', 'phase': 'starting_candidate',
                                              'profile_id': 'fixture', 'suite_id': 'fixture'}
                manager._save()
                await manager.close()
                restarted = Manager(Path(folder))
                await restarted.start()
                await asyncio.to_thread(process.wait, 2)
                self.assertEqual(restarted.history()[0]['status'], 'interrupted')
                self.assertIsNone(restarted.status()['operation'])
                await restarted.close()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    async def test_exclusive_state_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Manager(Path(folder)), Manager(Path(folder))
            await first.start()
            try:
                with self.assertRaisesRegex(RuntimeError, 'another manager'):
                    await second.start()
            finally:
                await first.close()


class EvaluationChecks(unittest.IsolatedAsyncioTestCase):
    async def test_missing_reset_or_cleanup_never_passes(self):
        class Response:
            status_code = 404
            text = 'unsupported'

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, *args, **kwargs):
                return Response()

        suite = {'cases': [{'name': 'test'}]}
        async def passed_case(client, route, case, phase, **kwargs):
            return {'passed': True, 'phase': phase}
        for reset_status, cancellation in (
            (404, {'passed': True}),
            (200, {'passed': False, 'inconclusive': True}),
        ):
            Response.status_code = reset_status
            with patch('inference_deploy.checks.httpx.AsyncClient', return_value=Client()), \
                    patch('inference_deploy.checks.request_case', side_effect=passed_case), \
                    patch('inference_deploy.checks.cancellation_recovery', new=AsyncMock(return_value=cancellation)):
                result = await evaluate({'url': 'fixture'}, suite)
            self.assertFalse(result['passed'])
            self.assertTrue(result['inconclusive'])


if __name__ == '__main__':
    unittest.main()
