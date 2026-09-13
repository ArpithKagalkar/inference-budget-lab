# MVP verification — 2026-09-09

Environment: Windows, Python 3.13.5, Node.js 22.16.0. No paid model requests were made.

## Automated checks

`python -m unittest discover -s tests -v`: **14 tests passed**.

Coverage includes deterministic runs, nonidentical cross-split prompts, validation-only threshold selection, strict schema scoring, queue growth under load, impossible constraints, invalid configuration, text-only difficulty features, actual HTTP requests to a mock provider, token cost calculation, missing usage handling, billed malformed responses, complete background job persistence, CSV row count, cross-origin rejection, static file allowlisting, and API health.

`node --check web/app.js`: passed.

An integration test found a SQLite connection lifecycle bug on Windows; the connection context now explicitly closes handles. The startup smoke test found a Windows console encoding incompatibility; console startup text now uses ASCII.

## Browser checks

Verified with Playwright in a real Chromium browser:

- Run a default experiment and render chart, comparison, confidence intervals, routing distribution, segments, and validation audit.
- Change arrival rate, worker count, and traffic shape; save an unaccepted burst experiment.
- Open saved history and restore a prior accepted experiment.
- Filter request explorer to quality failures, then search down to one matching ticket.
- Download JSON from the dashboard; CSV endpoint verified structurally by API tests.
- Restore saved results after a server restart and browser reload.
- Desktop 1440×1100 and mobile 390×844: visually inspected, no document horizontal overflow.
- Final browser console: **0 errors, 0 warnings**.

Screenshots are in `output/playwright/dashboard-desktop.png` and `output/playwright/dashboard-mobile.png` (ignored by Git).

## Reproducible simulated baseline

Seed 42, 160 test fixtures, 120 validation fixtures, 4 requests/second, 8 client workers, steady arrivals, quality floor 90%, p95 SLO 1,800 ms.

| Policy | Exact match | p95 | Modeled token cost / 1k |
|---|---:|---:|---:|
| Always strong | 98.75% | 1,082 ms | $0.5400 |
| Always economy | 79.38% | 277 ms | $0.0324 |
| Adaptive | 93.75% | 934 ms | $0.1607 |

Selected threshold: 0.6. Modeled savings: 70.2%. Adaptive quality Wilson 95% CI: approximately 88.9%–96.6%; its lower bound is below the quality floor. Acceptance uses the point estimate, as documented. The burst configuration at 12 requests/second and 2 workers is unaccepted, demonstrating the operating-point boundary rather than assuming the router always succeeds.

These figures are **synthetic simulation outputs**, not LLM or GPU benchmark results. Live provider compatibility was initially tested against a local mock HTTP service. The later pilot below verifies local Ollama inference; real-world labeled data and actual infrastructure-cost savings remain unverified.

## Local-model pilot added after the original MVP verification

The first Qwen3 0.6B/4B live pilot revealed that prompt v1 listed categories without defining their semantics. The 4B model consistently mapped nonfunctional buttons and corrupt exports to `performance` while the fixture rubric expected `bug`. That run remains in history as an unaccepted diagnostic result. Prompt v2 makes the fixture taxonomy explicit; subsequent runs record `prompt_version` so results are not silently compared across contracts.

Prompt v2 pilot `85b89ba9de85` used Qwen3 0.6B and 4B through Ollama on an RTX 4050 Laptop GPU with 6 GB VRAM. `ollama ps` reported both models at 100% GPU. Configuration: 30 validation fixtures, 20 held-out test fixtures, seed 42, one client worker, 1.5 requests/second, 90% exact-match floor, and 5,000 ms p95 SLO. Cost uses measured response time at a normalized $1 per occupied GPU-hour; it is a comparison unit rather than an electricity or rental bill.

| Policy | Test exact match | 95% Wilson CI | Test p95 | Normalized cost / 1k | Feasible on test |
|---|---:|---:|---:|---:|---:|
| Always strong (4B) | 95% | 76%–99% | 6,493 ms | $0.203 | No |
| Always economy (0.6B) | 55% | 34%–74% | 1,429 ms | $0.096 | No |
| Adaptive fallback | 95% | 76%–99% | 6,250 ms | $0.201 | No |

No validation candidate met both constraints. Strong-only validation reached 86.7% exact match with 7,309 ms p95, so the router correctly fell back and marked the experiment unaccepted. This result shows that the 4B local model/hardware pair is insufficient for the selected 90%/5-second operating point. The 20-request test interval is wide; it is a compatibility pilot, not a final portfolio claim.

## InferenceOps V1 verification — 2026-09-13

No paid model requests, production data connections, merges, deployments, or real Slack deliveries were made.

### Automated and data checks

`python -m unittest discover -s tests -v`: **27 tests passed**. The additional coverage includes normalized trace validation and duplicate rejection; all three detectors; generic task scoring; confidence fallback and combined-call accounting; deterministic Bitext stratification and validation/test separation; hosted confirmation and worst-case budget refusal; version-1 SQLite migration; workflow transition rules; staged notification labeling; GitHub allowlisting and PR idempotency; and the complete orchestration API path.

`node --check web/app.js`: passed.

The pinned Bitext source downloaded successfully: revision `430d1a89bd93bd1fa23c16f29dd53e73f0087443`, SHA-256 `6f81102b0100b97b8468eb04368033a23206bf1fde9d53500d5806ec1001a434`. The generated manifests contain 270 validation cases, 540 held-out test cases, and 135 pending-review examples. Their labels remain explicitly identified as published dataset labels.

### Local Ollama integration gate

The Bitext integration pilot used 10 fixed validation and 10 fixed held-out cases with Qwen3 0.6B as economy and Qwen3 4B as strong. Ollama's native structured-output endpoint ran with thinking disabled and a normalized $1/GPU-hour comparison rate. Both tiers returned 100% schema-valid responses with zero request errors.

| Policy | Exact match | p95 latency | Normalized cost / 1k | Accepted |
|---|---:|---:|---:|---:|
| Always strong (4B) | 20% | 1,137 ms | $0.289 | No |
| Always economy (0.6B) | 20% | 625 ms | $0.141 | No |
| Economy with fallback | 20% | 625 ms | $0.141 | No |

The candidate was correctly rejected because it missed even the temporary 50% pilot quality floor. The apparent 51.3% cost difference is not a verified saving. The small sample is an adapter/integration check, not a statistical model comparison; the full 270/540 replay and manual label review remain necessary before making a quality claim.

### GitHub and staged notification proof

The public sandbox repository is [ArpithKagalkar/inferenceops-demo-support-service](https://github.com/ArpithKagalkar/inferenceops-demo-support-service). Workflow `dcc85333137a` created and read back [draft PR #1](https://github.com/ArpithKagalkar/inferenceops-demo-support-service/pull/1) from branch `inferenceops/optimization-dcc85333137a`. Verification confirmed draft state and an exact two-file change set: `config/inference-policy.json` and `evidence/dcc85333137a.json`. The evidence file labels the source experiment as simulation. The follow-up Slack-equivalent message was stored as staged local outbox evidence and never represented as delivered.

### Browser checks

Playwright verified the Overview, Opportunities, Experiment Lab, Workflows, and Request Explorer flows in Chromium. A new 80-request simulation completed and rendered its evidence. Desktop 1440×1100 and mobile 390×844 were inspected; at mobile width the document measured 375 pixels inside a 390-pixel viewport, so no horizontal overflow was present. Final browser console: **0 errors, 0 warnings**.

Screenshots are stored in ignored local evidence at `output/playwright/inferenceops-overview-desktop.png`, `output/playwright/inferenceops-workflow-desktop.png`, and `output/playwright/inferenceops-mobile.png`.
