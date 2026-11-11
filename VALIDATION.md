# Validation on 2026-11-11

The real backend was llama-server 0.7.0 build 11669 (620d2225c), running Qwen2.5-0.5B-Instruct Q4_K_M on an Apple M4 Mac. The 491,400,032-byte GGUF was freshly downloaded from Qwen's pinned Hugging Face revision and its SHA-256 verified against published metadata. See evidence/model-manifest.json. Runtime arguments requested 99 GPU layers and one slot; the installed runtime enumerates Apple M4 as MTL0. No per-operator GPU trace was collected.

## Actual upgrade and rollback flow

tests/live_flow.py exercised the actual installed CLI, FastAPI service, llama-server and OpenAI-compatible HTTP gateway:

- A fresh manager rejected inference while no model was loaded.
- Registering immutable profiles and a three-case application suite worked. Checks covered exact READY, real JSON Schema, and a prompt exceeding a 128-token context.
- Applying without successful qualification returned conflict.
- The 2048-context profile qualified and served real inference through the stable gateway.
- The 128-context profile was rejected, and the prior server was restored and verified.
- The 3072-context candidate qualified, while staging restored the active profile afterward.
- Apply waited for an existing stream to drain. During maintenance the gateway returned 503 to new requests. Closing the client stream released the lease.
- Successful apply routed subsequent real inference to the candidate's profile header and runtime alias.
- Manual rollback restored the previous profile and verified a successful inference response.
- An externally delivered SIGKILL terminated the actual candidate during startup. The manager automatically restored the old profile and served a validated response.
- SIGKILL of the manager followed by restart recovered the committed deployment.
- All failed and completed operations remained in history.

The final complete run is evidence/live-flow-03. Earlier attempts remain retained. live-flow-01 exposed a test harness expecting HTTP200 instead of201 for creation. live-flow-final correctly returned inconclusive after the stronger evidence gate detected that the actual llama-server required --slot-save-path for slot erasure. The manager now creates and owns that directory and the full flow was rerun successfully.

## Crash-boundary matrix

tests/live_recovery.py exercised eight deterministic SIGKILL points after actual persisted writes: preflight, draining, stopping_active, starting_candidate, after_spawn, postcheck, committing, and after_commit.

Each restart cleaned recorded owned processes, recovered the intended profile and served an actual READY inference response. Before the commit point, the baseline recovered. After the commit point, the candidate recovered and manual rollback succeeded. See evidence/crash-recovery-01/results.json.

The SIGKILL hook exists only in tests/crash_manager.py and terminates the test manager after its normal journal write. The serving code has no fault-injection switch.

## Streaming cancellation

A separate real request through the stable gateway was disconnected after its first content event. The gateway lease reached zero, the actual backend slot was observed idle, and the same process served a validated READY response afterward. See evidence/proxy-cancellation.json. Individual KV block reclamation remains inconclusive.

## Focused and browser checks

Focused tests cover HTTP SSE bytes, upstream errors, cancellation, maintenance rejection, departed control clients, schema/argument validation, exclusive ownership and OS child cleanup. Tests using fixture backends are identified as fixtures and are separate from the real model runs above.

The dashboard was exercised against the actual service to create profiles and suites, qualify, apply, and inspect serving state. Responsive layout and validation were checked at desktop and mobile widths. Further completed dashboard actions and final test counts are recorded in evidence/validation-summary.json.

A targeted review found executable script wrappers cannot be identified reliably by the native process ownership rule. The preview now rejects scripts before staging and repeats the native-executable check for saved profiles. Paths with spaces are covered by a real OS child test.

## Limits

Validation uses one model, one native runtime build and context-setting upgrades on one Mac. Different checkpoints, runtime-version upgrades, Linux, Windows, multiple backend slots, multi-node canaries, and multi-user remote administration were not validated. The manager intentionally supports POSIX native llama-server and one slot.

Application checks are declarative text/JSON checks. Arbitrary tool execution, code-test sandboxes and universal semantic correctness are outside this preview. Latency is the total response time for a single case, without statistical capacity qualification. These results do not establish production reliability under every failure or workload.
