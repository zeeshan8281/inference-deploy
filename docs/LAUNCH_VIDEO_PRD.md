# Inference Deploy — 30-second launch video PRD

## Paste this brief into Claude

Build a finished, exactly 30-second motion-design launch video for **inference-deploy**, an open-source local inference deployment manager. The video should make an inference engineer want to inspect the repository because it demonstrates a concrete operational problem: qualifying a configuration change, moving traffic safely, and recovering when an upgrade fails.

Use a dense, carefully composed **bento dashboard** with real animated line charts, counted token blocks, deployment-state transitions, and an evidence matrix. This is a launch film, with sharp typography and deliberate pacing. Keep the same recognizable grid throughout; change emphasis and contents inside its panels. Deliver the rendered MP4 and editable source, not only a storyboard or an HTML mockup.

Primary hook: **“Your model upgrade failed. Now what?”**

Final message: **“Qualify. Deploy. Recover.”**

CTA: **github.com/zeeshan8281/inference-deploy**

Use the supplied evidence files. Do not invent measurements, token boundaries, recovery durations, UI capabilities, or benchmarks. If the renderer cannot access the repository, ask for the files listed below instead of substituting invented data.

## Audience and purpose

For developers running local GGUF models with llama-server, inference engineers, and builders who change context settings or serving configurations. Explain what the product does, demonstrate that the lifecycle was tested with a real model, then give viewers a readable repository link. Prioritize operational behavior over throughput bragging.

The product is a standalone Python project with a dashboard, CLI, and stable OpenAI-compatible gateway. Immutable profiles bind the binary, checkpoint and arguments to hashes. Application checks gate apply. Deployments drain in-flight requests and have a maintenance interval. Failed upgrades restore and verify the previous committed profile; restart reconciles interrupted operations from durable state.

The tested upgrade changes context from 2048 to 3072 with the same model and runtime build. A 128-context configuration is rejected by a longer-prompt check. Another test kills the actual candidate during startup and verifies automatic rollback. These are distinct tests; do not present the rejected configuration as an already-committed deployment or imply it caused the injected startup crash.

## Files to use

All paths below are relative to the repository root:

| File | Purpose |
|---|---|
| `docs/launch-video-data.json` | Small, extracted dataset for charts and token counts; use this as the rendering input. |
| `evidence/live-flow-03/results.json` | Original lifecycle receipts and per-case measurements. |
| `evidence/crash-recovery-01/results.json` | Eight actual crash-boundary recovery results. |
| `evidence/proxy-cancellation.json` | Actual gateway disconnect, idle-slot observation and subsequent inference. |
| `evidence/validation-summary.json` | Focused-test counts, browser checks, environment and limits. |
| `evidence/dashboard.jpg` | Real dashboard after a successful rollback; visual reference and proof. |
| `VALIDATION.md` and `README.md` | Behavior, reproduction commands and limitations. |

Repository: https://github.com/zeeshan8281/inference-deploy

## Visual system

- Canvas: 1920×1080, 60 fps, exactly 1800 frames. H.264 MP4, yuv420p; include editable project and local font assets. A 1080×1350 social crop is optional, after the main export passes review.
- Background `#0E1116`; cards `#171D26`; borders `#2B3542`; primary type `#F3F5F7`; muted type `#A7B2C1`. Mint `#A5D6B8` means accepted/serving, amber `#E9B868` means maintenance, coral `#F27972` means rejected/failed, blue `#82ACF6` means cache/prefix data.
- Use Inter or an equivalent clean sans; use IBM Plex Mono or equivalent for values, tokens and state labels. Headline 80–100 px, main metrics 64–84 px, panel titles 28–32 px, necessary captions at least 24 px. Avoid paragraphs.
- 64 px outer safe margins, 24 px gutters, 20 px card radii, restrained shadows. Six panels on a 12-column grid: main lifecycle/chart panel 7×2 units, token panel 5×1, deployment panel 5×1, three compact evidence panels below. Keep proportions balanced and readable.
- Typography, charts and token blocks provide detail. Keep texture almost invisible. No laptop mockup, generic AI brain, floating decorative code, stock footage, particle fog or unrelated graphics.
- Brand only as **inference-deploy**. Do not add “Zeeshan8281 inference engineering,” a personal logo, avatar or invented tagline. The username appears only in the repository URL.

## Exact storyboard

| Time | Main copy | Motion and supporting panels |
|---|---|---|
| 0.0–3.0 s | **Your model upgrade failed. Now what?** | Start directly in the bento grid. Candidate status turns coral: `STARTUP FAILED`. A nearby previous-profile card stays visible. Pulse the affected connection once. Small caption: `Replay of a tested failure path`. No fake terminal wall or typing intro. |
| 3.0–6.0 s | **inference-deploy** / `Qualify. Deploy. Recover.` | Route diagram resolves into `App → stable /v1 endpoint → managed llama-server`. Highlight profile hashes and the three actions in sequence. Other cards begin displaying the actual environment: `Apple M4`, `Qwen Q4_K_M`, `one active runtime`. |
| 6.0–12.0 s | **Checks before traffic.** | Main card draws the three real latency traces described below, with visible axes and markers. Token card shows fresh vs warm prefix counts. Third card marks `READY`, `JSON schema`, `Long prompt` as passed. Brief badge: `128-context profile rejected`. Small persistent chart caption: `One qualification run · complete response time`. |
| 12.0–17.0 s | **Drain. Apply. Verify.** | Animate stable `2048` → candidate `3072`, with lifecycle chips `DRAIN → START → CHECK → COMMIT`. A single existing stream finishes before its runtime stops. New request is labeled `503 · maintenance`; then serving moves to the candidate and a request returns `READY`. This is an illustrative lifecycle replay, not a measured availability trace. |
| 17.0–22.0 s | **Kill the candidate. Restore the previous profile.** | Return to the startup-failure scenario introduced in the hook. Candidate receives a `SIGKILL` marker during startup, then the recovery connector moves back to the baseline. Show `Previous profile verified` and actual response `READY`. Keep `Maintenance interval` visible while unavailable. Do not imply the earlier successful commit automatically rolled back later. |
| 22.0–26.0 s | **8 crash boundaries. 8 successful recoveries.** | Eight cells light up from the real matrix. Compact supporting cards show `16 focused tests passed` and `Browser apply + rollback verified`. Label crash cells with short human labels; use the exact phases in the data underneath or in the source. No “100% reliable” claim. |
| 26.0–30.0 s | **Qualify. Deploy. Recover.** | All cards settle into the finished grid. Product name and GitHub URL dominate. Keep the complete URL readable for the full final four seconds. Footer: `Local preview · one model/runtime build tested · maintenance interval`. End cleanly on the last frame. |

The sequence compresses separate test scenarios. Use clear `Successful upgrade`, `Failed startup`, and `Crash recovery tests` labels to prevent accidental causal claims. Five seconds of animation must never be presented as five seconds of measured recovery.

## Real line-chart specification

Plot the **stable profile qualification only**, sourced from `stable_qualified`. X axis is categorical: `Fresh prefix`, `Prime`, `Warm prefix`; Y axis is `Complete response (ms)`, zero-based, 0–200 ms. Use straight segments joining discrete measured points; three labeled traces. Do not interpolate imaginary samples or render a continuous time-series waveform.

| Check | Fresh prefix | Prime | Warm prefix |
|---|---:|---:|---:|
| READY | 72.34 ms | 22.05 ms | 21.17 ms |
| JSON | 87.60 ms | 55.17 ms | 53.18 ms |
| Long prompt | 189.05 ms | 18.50 ms | 18.24 ms |

Render from the full-precision JSON values and round labels consistently. This illustrates evidence collected by qualification, including prefix-cache behavior. It is not a controlled comparison between product versions, a speedup caused by the manager, a TTFT chart, an aggregate benchmark or a p95 claim. No invented error bars. Fresh prefix means slot erase with cache reuse disabled, not cold OS caches.

Animate axes first, then markers and segments over roughly two seconds. Hold the completed plot long enough to read. Draw real curves at least 4 px wide with 10–12 px markers and a clear legend. Do not fill the plot with distracting gradients or unreadable microtext.

## Token split specification

Use the READY case for a readable counted-block animation:

- Fresh request: **37 prompt tokens**, **0 cached**, **2 output tokens**.
- Warm request: **37 prompt tokens**, **36 cached**, **1 prompt token evaluated**, **2 output tokens**.
- Show 37 equal-sized prompt blocks as a compact grid. Change 36 to blue for the warm case and leave one outlined. Keep two mint output blocks in a separate lane with the response text `READY` above them.
- Use labels `Prompt 37`, `Cached 36`, `Output 2`. Cached tokens are a subset of prompt tokens, not 36 additional prompt tokens.
- No guessed subword strings: the receipts contain counts, not token IDs or decoded boundaries. Show abstract counted blocks. Do not split READY into fabricated token text.
- This is request accounting and observed prefix reuse, not proof of individual KV-block reclamation.

## Crash matrix

Eight tested phases, in order: `preflight`, `draining`, `stopping_active`, `starting_candidate`, `after_spawn`, `postcheck`, `committing`, `after_commit`.

All eight recovered the expected profile and served a real `READY` inference response. Before commit, the baseline recovers. After commit, the candidate recovers; manual rollback then succeeds. Represent the eighth cell accurately with `Committed candidate recovered`, rather than claiming every crash restores the old profile.

Use a crisp 2×4 status matrix. This is binary outcome evidence, not a measured downtime graph. Keep `16 focused tests` visually separate; those include fixture tests and are not sixteen additional real-model crash cases.

## Optional voiceover

Use this exact script if a voiceover is included, with clean on-screen captions. Keep it conversational and under 30 seconds:

> Your model upgrade failed. Now what? Inference Deploy checks configurations before they serve traffic. It drains requests, applies the change, and verifies real model responses. If startup fails, it restores and checks the previous profile. We killed the manager at eight deployment boundaries. All eight recovered and served inference. Qualify. Deploy. Recover. The code and receipts are on GitHub.

Music: restrained, rhythmic electronic bed. Short transition hits at apply, injected failure, and verified recovery. Avoid sirens, meme sounds and a cinematic voice that makes the preview sound like a production guarantee. The film must remain understandable muted.

## Claims and scope

Safe claims: open-source local preview; separate project; real llama-server/model validation; gated configuration changes; drain before stop; manual rollback; automatic restore after failed startup; durable operation receipts; eight tested crash boundaries recovered.

Do not claim zero downtime, production readiness, universal rollback safety, multi-GPU/cloud orchestration, multi-node canaries, remote authenticated teams, throughput improvement, training, or different-checkpoint/runtime-version upgrades having been tested. This was inference with Qwen on an M4, not GPU training or a Modal benchmark. One model and one runtime build were validated. A maintenance interval is part of the design.

## Acceptance and handoff

1. Return `inference-deploy-launch-30s.mp4`, editable source, the exact data JSON used, and a short render README. Use a deterministic frame-based timeline; Remotion is suitable if available, but do not add a backend or fetch live data during rendering.
2. Verify with ffprobe: 1920×1080, 60 fps, 1800 frames, 30 seconds; H.264/yuv420p. Check audio length if present. Report the actual checks performed.
3. Inspect stills at 0, 7, 10, 15, 20, 24 and 28 seconds. No clipped text, unrendered fonts, empty charts, overlapping cards, off-canvas URL or unsupported numerical claims.
4. Watch the complete rendered export with and without sound. Confirm the success and failure paths are distinct, the chart is legible, token counts match JSON, and the repository URL remains visible for the final four seconds.
5. Do not replace the bento composition with a slideshow, a single laptop illustration, or static cards that only fade in. The line chart, prefix blocks, traffic route and recovery matrix must each carry meaningful motion.
6. If any export or validation step cannot be completed, name the missing step plainly. A preview page or a fixture screenshot is not a finished video.
