# Inference Deploy

Operate a local llama-server endpoint, qualify configuration changes against application checks, and apply or roll back a deployment with a durable history.

The operator dashboard and the OpenAI-compatible gateway share one localhost service. Profiles bind the native runtime executable, GGUF model and launch arguments to SHA-256 hashes. Qualification runs actual inference before apply is enabled. Applying a change drains existing streams, starts the candidate, reruns its acceptance suite, and either commits the new profile or restores and verifies the previous one.

## Start

Requires Python 3.12+, a native llama-server binary and a local GGUF model. The tested system is macOS 26.6.2 on an Apple M4 with llama.cpp 0.7.0, build 11669, commit 620d2225c. POSIX process locking and process groups are required. Shell/Python runtime wrappers are unsupported.

```bash
uv sync --locked
.venv/bin/inference-deploy serve --state-dir .state/default --port 8088
```

Open [the dashboard](http://127.0.0.1:8088). Create a profile using absolute paths to the native llama-server executable and GGUF file. For example, use runtime arguments:

```json
["--ctx-size","2048","--n-gpu-layers","99","--parallel","1"]
```

Add an acceptance suite using the built-in template or [examples/application.json](examples/application.json). Choose that suite on a profile, run qualification, inspect the receipt, then apply. Create another immutable profile to change arguments. After a successful upgrade, **Roll back** restores the preceding committed profile.

One host and one active runtime are supported. Qualification also temporarily stops the active model to keep staging isolated. New requests receive HTTP 503 with Retry-After during maintenance. Active requests drain before their runtime stops. A drain timeout rejects the operation and retains the current service.

## Use the stable endpoint

Configure your client with base URL `http://127.0.0.1:8088/v1`. Requests are pinned to one runtime for the entire response. The response header `X-Inference-Profile` identifies that profile. The gateway replaces the supplied model name with the exact managed runtime alias.

```bash
curl http://127.0.0.1:8088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"application","messages":[{"role":"user","content":"Reply with exactly READY."}],"max_tokens":16,"temperature":0,"stream":true}'
```

The gateway forwards raw SSE and propagates client disconnects upstream. Only models and chat completions are proxied in this version. Control APIs are localhost-only with same-origin browser checks. This is a local operator service without remote multi-user authentication.

## Qualification and evidence

Suites support text contains, equality after trimming outer whitespace, and JSON Schema Draft 2020-12 without schema references. They do not execute arbitrary code or tools. Each case records a slot reset, a fresh-prefix request, an explicit priming request and a warm-prefix request. Complete-response latency budgets are optional. A separate streaming disconnect must produce an observed idle backend slot and successful application recovery.

Missing reset or slot evidence yields an inconclusive qualification. Application failures reject it. Apply requires the most recent qualification for the exact profile/suite to pass, checks artifact hashes again, and repeats acceptance after startup. Restoring a previous deployment reruns its full suite. A recovery failure leaves the endpoint unavailable with an explicit error.

State is atomically replaced and fsynced. On manager restart, recorded owned processes are reconciled, an interrupted operation is retained, and the last committed profile is started and verified. The state-directory lock prevents two managers from owning the same endpoint state. Keep the model and native runtime artifacts available for recovery.

Receipts include prompt content, outputs, local paths and logs. Inspect them before sharing. Fresh-prefix means the server slot was erased and cache reuse disabled for that request. OS and compilation caches are outside that reset. Slot-idle observation does not establish per-block KV reclamation. Single-case latency samples do not establish a capacity or tail-latency guarantee.

## CLI and HTTP API

```bash
.venv/bin/inference-deploy status
.venv/bin/inference-deploy profiles
.venv/bin/inference-deploy suite examples/application.json
.venv/bin/inference-deploy qualify PROFILE_ID SUITE_ID
.venv/bin/inference-deploy apply PROFILE_ID SUITE_ID
.venv/bin/inference-deploy rollback
.venv/bin/inference-deploy history
```

Use `--url http://127.0.0.1:PORT` before the subcommand for another management port. Profile registration uses `inference-deploy profile path/to/profile.json`. Long operations return a completed receipt and remain active if the browser that started them disconnects.

Management API endpoints are `/api/status`, `/api/profiles`, `/api/suites`, `/api/history`, `/api/qualify`, `/api/apply`, and `/api/rollback`. The local OpenAPI schema is at `/docs`.

## Validation

[VALIDATION.md](VALIDATION.md) describes the real model upgrade/rollback flow, process-failure injection, crash recovery at eight journal boundaries, browser checks and remaining limits.

The [30-second launch video brief](docs/LAUNCH_VIDEO_PRD.md) includes the storyboard, motion requirements and claim limits. Its [chart dataset](docs/launch-video-data.json) is extracted from the committed real-run receipts.

```bash
uv sync --locked --extra dev
.venv/bin/pytest -q
.venv/bin/python tests/live_flow.py --model /absolute/model.gguf --output evidence/my-live-flow
.venv/bin/python tests/live_recovery.py --state evidence/my-live-flow/state --output evidence/my-crash-test
```

The live scripts create dedicated state and logs, launch actual model processes and terminate only their managed processes. Crash recovery tests use a test-only SIGKILL harness after real journal writes. Run these checks with disposable test deployments.

Code is MIT licensed. The external runtime and checkpoint retain their own licenses.
