# InferenceOps

[![CI](https://github.com/ArpithKagalkar/inference-budget-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/ArpithKagalkar/inference-budget-lab/actions/workflows/ci.yml)

**An evidence-first AI FinOps agent that finds inference waste, tests safer alternatives, and prepares a verified draft change.**

```text
Traces → Cost audit → Opportunity → Guarded experiment → PR preview → Draft PR → Staged notification
```

The original Inference Budget Lab remains the evaluation engine. It compares strong-only, economy-only, and adaptive policies under quality and p95 latency constraints. InferenceOps adds production-shaped traces, opportunity detection, durable workflow states, provider budget controls, and restricted external actions.

## Run locally

Requires Python 3.10+ and a modern browser. Simulation and the demo workspace need no API key or package installation.

```powershell
git clone https://github.com/ArpithKagalkar/inference-budget-lab.git
cd inference-budget-lab
python -m lab.server
```

Open **http://127.0.0.1:8787**, then select **Load demo workspace**. The demo imports 900 explicitly synthetic traces and detects premium-model overuse, repeated system prompts, and excessive RAG context. Only the model-routing finding can start an automated V1 experiment.

## Verify

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
python -m lab.benchmark --requests 160 --seed 42 --rps 4 --concurrency 8
```

## Public evaluation data

Download the pinned Bitext Customer Support dataset and create deterministic intent-stratified manifests:

```powershell
python -m inferenceops.traces.bitext
```

This prepares 270 validation and 540 held-out test cases plus a 135-item review queue under ignored `data/external/`. Labels are reported as **published dataset labels** until review is completed. See [data provenance](docs/data-provenance.md).

## Real models

The Budget Lab supports any two endpoints implementing `POST /v1/chat/completions`. The intent benchmark evaluates economy-first confidence fallback against the Bitext task:

```powershell
python -m inferenceops.experiments.intent_benchmark --validation 30 --test 20
```

For local Ollama, use the [local run guide](docs/local-live-run.md). Hosted providers require explicit token prices, worst-case cost preflight, and a typed confirmation such as `RUN 5.00 USD`. API keys are never saved in SQLite or exports. No paid requests run at startup.

Qwen3 models should use Ollama's native structured-output path so reasoning does not consume the JSON token allowance:

```powershell
$env:ECONOMY_BASE_URL='http://127.0.0.1:11434/v1'
$env:ECONOMY_MODEL='qwen3:0.6b'
$env:ECONOMY_HOURLY_COST='1'
$env:ECONOMY_OLLAMA_THINK='false'
$env:STRONG_BASE_URL='http://127.0.0.1:11434/v1'
$env:STRONG_MODEL='qwen3:4b'
$env:STRONG_HOURLY_COST='1'
$env:STRONG_OLLAMA_THINK='false'
python -m inferenceops.experiments.intent_benchmark --validation 10 --test 10
```

## External actions

GitHub writes are restricted to `ArpithKagalkar/inferenceops-demo-support-service`. A workflow must pass and generate a preview before it can request a real draft PR. The connector verifies the branch, draft state, and exact two-file allowlist after creation. It cannot merge or deploy.

The V1 proof repository is public at [inferenceops-demo-support-service](https://github.com/ArpithKagalkar/inferenceops-demo-support-service), and the verified automation artifact is [draft PR #1](https://github.com/ArpithKagalkar/inferenceops-demo-support-service/pull/1).

Slack defaults to a persisted local outbox. A real webhook requires separate configuration and explicit delivery confirmation. Langfuse support is staged as a normalized export importer; direct account access is not enabled in V1.

## API

| Route | Purpose |
|---|---|
| `GET /api/health` | Runtime and provider readiness |
| `POST /api/runs` | Start a simulation or confirmed live run |
| `GET /api/jobs/{id}` | Poll a background run |
| `GET /api/runs/{id}` | Complete experiment evidence |
| `POST/GET /api/trace-batches` | Import and list trace batches |
| `GET /api/summary` | Latest spend and workflow summary |
| `POST /api/audits` | Run the deterministic detectors |
| `GET /api/opportunities` | List explainable findings |
| `POST /api/opportunities/{id}/experiments` | Run an eligible guarded simulation |
| `GET /api/workflows/{id}` | Read state and immutable events |
| `POST /api/workflows/{id}/pr-preview` | Generate an allowlisted change preview |
| `POST /api/workflows/{id}/create-pr` | Create and verify a confirmed draft PR |
| `POST /api/workflows/{id}/notify` | Stage or explicitly deliver a notification |
| `POST /api/provider-preflight` | Estimate calls and maximum configured cost |

## Evidence labels

- **Observed:** calculated from an imported trace window.
- **Estimated:** detector potential, not experimentally verified.
- **Simulated:** deterministic system evidence, not real-model performance.
- **Live:** calls and latency measured against configured endpoints.
- **Verified:** a candidate passed every configured constraint with complete accounting.

InferenceOps V1 does not connect production traffic, merge pull requests, deploy changes, or perform rollback. A production claim requires independently reviewed labels, shadow traffic, a controlled canary, and monitored outcomes.

## Layout

```text
lab/                 Inference Budget Lab engine and legacy simulator
inferenceops/        Traces, audits, experiments, workflows and connectors
web/                 Framework-free responsive product dashboard
tests/               Engine, API, detector, workflow and connector tests
docs/                Architecture, provenance and verification evidence
data/                Local SQLite and downloaded datasets (ignored)
outputs/             Generated benchmark evidence (ignored)
```

See [InferenceOps architecture](docs/inferenceops-architecture.md) and the [verification log](docs/verification.md).
