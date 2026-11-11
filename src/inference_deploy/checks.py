"""Real application acceptance and dedicated-server recovery observations."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError


def suite_data(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - {'name', 'cases'}:
        raise ValueError('invalid suite fields')
    if not isinstance(data.get('name'), str) or not data['name'].strip():
        raise ValueError('suite name is required')
    cases = data.get('cases')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise ValueError('suite requires 1 to 20 cases')
    names = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) - {'name', 'messages', 'expect', 'max_tokens', 'max_latency_ms'}:
            raise ValueError('invalid case fields')
        if not isinstance(case.get('name'), str) or not case['name'] or case['name'] in names:
            raise ValueError('case names must be present and unique')
        names.add(case['name'])
        messages = case.get('messages')
        if not isinstance(messages, list) or not messages or len(messages) > 100:
            raise ValueError('messages must be a nonempty list')
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                    or message['role'] not in ('system', 'user', 'assistant')
                    or not isinstance(message['content'], str)):
                raise ValueError('messages require text content and a supported role')
        expect = case.get('expect')
        if not isinstance(expect, dict) or not expect or set(expect) - {'contains', 'equals', 'json_schema'}:
            raise ValueError('expect requires contains, equals, or json_schema')
        for field in ('contains', 'equals'):
            if field in expect and not isinstance(expect[field], str):
                raise ValueError(f'{field} must be text')
        if 'json_schema' in expect:
            schema = expect['json_schema']
            if '$ref' in json.dumps(schema) or '$dynamicRef' in json.dumps(schema):
                raise ValueError('external and recursive schema references are unsupported')
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise ValueError("invalid JSON schema") from exc
        tokens = case.get('max_tokens', 32)
        if type(tokens) is not int or not 1 <= tokens <= 512:
            raise ValueError('max_tokens must be 1 to 512')
        deadline = case.get('max_latency_ms', 30000)
        if type(deadline) not in (int, float) or not 0 < deadline <= 300000:
            raise ValueError('max_latency_ms must be positive and at most 300000')
    return json.loads(json.dumps({'name': data['name'], 'cases': cases}, allow_nan=False))


def acceptance(text: str, expect: dict) -> list[str]:
    failures = []
    if 'contains' in expect and expect['contains'] not in text:
        failures.append('expected substring missing')
    if 'equals' in expect and expect['equals'] != text.strip():
        failures.append('response does not equal expected text after outer whitespace trimming')
    if 'json_schema' in expect:
        try:
            Draft202012Validator(expect['json_schema']).validate(json.loads(text))
        except (ValueError, ValidationError) as exc:
            failures.append('JSON schema check failed: ' + str(exc)[:500])
    return failures


async def request_case(client, route, case, phase, *, cache_prompt):
    started = time.monotonic()
    result = {'case': case['name'], 'phase': phase, 'cache_prompt_requested': cache_prompt,
              'latency_scope': 'gateway-to-backend complete nonstream response, including validation read'}
    try:
        response = await client.post(route['url'] + '/v1/chat/completions', json={
            'model': route['alias'], 'messages': case['messages'], 'temperature': 0,
            'max_tokens': case.get('max_tokens', 32), 'stream': False, 'cache_prompt': cache_prompt,
            'id_slot': 0,
        })
        result['http_status'] = response.status_code
        result['latency_ms'] = (time.monotonic() - started) * 1000
        if not response.is_success:
            result.update(passed=False, error=response.text[:4000])
            return result
        data = response.json()
        text = data['choices'][0]['message']['content']
        if not isinstance(text, str):
            raise ValueError('backend response has no text content')
        failures = acceptance(text, case['expect'])
        if result['latency_ms'] > case.get('max_latency_ms', 30000):
            failures.append('complete-response latency budget exceeded')
        result.update(passed=not failures, failures=failures, text=text, usage=data.get('usage'),
                      timings=data.get('timings'), model=data.get('model'))
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        result.update(passed=False, error=f'{type(exc).__name__}: {exc}',
                      latency_ms=(time.monotonic() - started) * 1000)
    return result


async def cancellation_recovery(client, route, case):
    result = {'scope': 'dedicated server: client disconnect, slot quiescence, actual application recovery',
              'kv_reclamation': 'inconclusive; no per-block ownership adapter'}
    try:
        started = time.monotonic()
        async with client.stream('POST', route['url'] + '/v1/chat/completions', json={
            'model': route['alias'], 'messages': [{'role': 'user', 'content': 'Write a detailed long story about a ship at sea.'}],
            'temperature': 0, 'max_tokens': 256, 'stream': True, 'id_slot': 0,
        }) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith('data: ') and line[6:] != '[DONE]':
                    payload = json.loads(line[6:])
                    choices = payload.get('choices', [])
                    if choices and choices[0].get('delta', {}).get('content'):
                        result['content_event_observed'] = True
                        result['disconnect_after_ms'] = (time.monotonic() - started) * 1000
                        break
        if not result.get('content_event_observed'):
            raise ValueError('stream ended before a content event')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            slots = await client.get(route['url'] + '/slots')
            if not slots.is_success:
                result['slot_cleanup'] = 'inconclusive; slots unavailable'
                break
            value = slots.json()
            result['slots_after_disconnect'] = value
            if isinstance(value, list) and value and all(row.get('is_processing') is False for row in value):
                result['slot_cleanup'] = 'observed_idle'
                break
            await asyncio.sleep(.1)
        if 'slot_cleanup' not in result:
            result['slot_cleanup'] = 'deadline_exceeded'
        recovery = await request_case(client, route, case, 'post-cancellation-recovery', cache_prompt=False)
        result['recovery'] = recovery
        result['passed'] = recovery['passed'] and result['slot_cleanup'] == 'observed_idle'
        result['inconclusive'] = recovery['passed'] and result['slot_cleanup'].startswith('inconclusive')
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        result.update(passed=False, error=f'{type(exc).__name__}: {exc}')
    return result


async def evaluate(route: dict, suite: dict) -> dict:
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(310, connect=5), trust_env=False) as client:
        for case in suite['cases']:
            erase = await client.post(route['url'] + '/slots/0?action=erase')
            reset = {'http_status': erase.status_code,
                     'body': erase.text[:1000], 'scope': 'llama-server slot erase before fresh-prefix request'}
            cold = await request_case(client, route, case, 'fresh-prefix', cache_prompt=False)
            cold['slot_reset'] = reset
            results.append(cold)
            prime = await request_case(client, route, case, 'warm-prime', cache_prompt=True)
            results.append(prime)
            warm = await request_case(client, route, case, 'warm-prefix', cache_prompt=True)
            results.append(warm)
        cancel = await cancellation_recovery(client, route, suite['cases'][0])
    reset_verified = all(row.get('slot_reset', {}).get('http_status') == 200 for row in results if row['phase'] == 'fresh-prefix')
    applications_pass = all(row['passed'] for row in results)
    inconclusive = applications_pass and (not reset_verified or cancel.get('inconclusive', False))
    return {'passed': applications_pass and cancel['passed'] and reset_verified,
            'inconclusive': inconclusive, 'fresh_reset_verified': reset_verified, 'cases': results,
            'cancellation_recovery': cancel,
            'limits': ['Single repetitions with explicit fresh-prefix request and warm priming; no latency distribution or capacity bound.',
                       'Slot erase status is recorded; OS and compile caches are not reset.',
                       'All declared application assertions must pass; this is not general model correctness.']}
