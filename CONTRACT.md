# Integration contract

Python package inference_deploy. Localhost-only service. Single process per state directory. Agent-owned files must stay within ownership boundaries.

## Core types
Profiles created as dictionaries:
{"name":"stable","runtime":"/absolute/llama-server","model":"/absolute/model.gguf","args":["--ctx-size","2048","--n-gpu-layers","99","--parallel","1"],"startup_timeout":60,"drain_timeout":15}
Manager controls --host, --port, --alias, --metrics, --slots and any equivalent short/env forms. Reject attempts to override manager flags. Runtime/model hashes are recorded on creation and checked on launch. Persist immutable profiles. Assigned id is content-derived or uuid, stable dictionary id, name, artifact hashes.
Suite dictionaries:
{"name":"application","cases":[{"name":"ready","messages":[{"role":"user","content":"Reply with exactly READY."}],"expect":{"contains":"READY"},"max_tokens":32,"max_latency_ms":30000}]}
Support contains, equals, json_schema (document supported subset or use actual validator dependency); match all declared checks. No shell/code execution. Optional max_latency_ms. Suite stored with immutable id. Cold/fresh and warm/primed phases each run real inference. Record measurements and raw answers/errors. Include actual context settings/readiness identity and cancellation/recovery result. No semantic/capacity guarantee.
Validation errors ValueError, conflicts RuntimeError. API maps to400/409, no backend500 exception details. KeyError ->404.

## Manager interface owned by lifecycle agent
Manager(state_dir: Path)
await manager.start() recovery and exclusive state-directory lock
await manager.close() graceful shutdown/release lock
manager.status() -> JSON serializable current state plus active profile, previous profile, phase, maintenance, inflight and operation, active runtime PID/URL
manager.list_profiles() -> list[dict]
manager.create_profile(data: dict) -> dict
manager.list_suites() -> list[dict]
manager.create_suite(data: dict) -> dict
manager.history() -> list[dict] with completed/incomplete runs
await manager.qualify(profile_id: str, suite_id: str) -> run dict (pass/reject/inconclusive status)
await manager.apply(profile_id: str, suite_id: str) -> run dict. Requires successful qualification for exact profile+suite; initial bootstrap also qualifies first. Maintenance-mode sequential runtime execution, rollback on actual candidate startup or postcheck failure. Persist every transition before action. Failed qualification cannot promote.
await manager.rollback() -> run dict. Use prior approved profile and its suite, actual verification before committing.
async with manager.lease() as route: route dict {"url":..., "profile_id":..., "alias":...}. Lease pins runtime while operation drains. Maintenance/no active -> RuntimeError. Counter released on cancellation. Manager condition owns inflight accounting.
Long-running actions serialized with conflict errors rather than implicit queue. Return run only when completed. UI may poll status during action.
On restart unfinished operation must restore last committed active profile (if any), clean only own uniquely marked child processes including fork/journal gap, retain interrupted run and restore actual verified service. Record PID and unique alias marker before/after spawn. Do not kill unrelated processes or PID-reused process. Detect and prevent multiple managers using same state directory.
Close stops owned current child after draining where possible. Journal active config persists for next startup.

## HTTP owned by gateway agent
create_app(state_dir: Path) -> FastAPI in server.py. Lifespan starts Manager, closes Manager. Attach app.state.manager.
GET /api/status -> manager.status()
GET/POST /api/profiles -> list or created dict
GET/POST /api/suites -> list or created dict
GET /api/history -> list
POST /api/qualify {"profile_id","suite_id"} -> run
POST /api/apply {"profile_id","suite_id"} -> run
POST /api/rollback -> run
GET /healthz -> management health
GET /v1/models -> current proxy models
POST /v1/chat/completions -> streaming/nonstreaming real backend. Capture lease before upstream call, replace model with route.alias, forward raw SSE and status/error details safely, preserve upstream cancellation, release on client disconnect. Add X-Inference-Profile header. Validate payload size (2MiB default), avoid buffering stream. HTTP timeouts bounded/configured appropriately. During maintenance return503 with Retry-After, never route midstream to a different process.
GET / serves static/index.html. Static assets under /static.

## UI owned by UI agent
Vanilla HTML/CSS/JS, no build dependency. API follows above. Dashboard endpoint state; profile creation form; suite creation/edit JSON; qualification/apply/rollback controls and history; poll while jobs run. Explain one-host maintenance and show all failed outcomes. Explicit confirmation when user invokes apply/rollback is UI product affordance; no extra assistant approval needed.
No invented metrics or fixture results.
