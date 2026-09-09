# Inference Budget Lab

[![CI](https://github.com/ArpithKagalkar/inference-budget-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/ArpithKagalkar/inference-budget-lab/actions/workflows/ci.yml)

**Find the cheapest LLM routing policy that meets a quality floor and a p95 latency SLO.**

A working local experiment workbench with a dependency-free Python backend, browser dashboard, SQLite experiment history, deterministic load simulation, and an OpenAI-compatible live provider adapter. Built around structured support-ticket extraction, where outputs can be scored against known labels.

## Run it

Requires Python 3.10+ and a modern browser. No packages or API keys are needed for simulation.

```powershell
git clone https://github.com/ArpithKagalkar/inference-budget-lab.git
cd inference-budget-lab
python -m lab.server
```

Open **http://127.0.0.1:8787**. Set constraints and click **Run experiment**. The service binds to loopback only. Stop with Ctrl+C. `--port 8788` selects another port.

```powershell
# Reproducible command-line benchmark
python -m lab.benchmark --requests 160 --seed 42 --rps 4 --concurrency 8 --quality-min 0.9 --latency-slo-ms 1800

# Pressure test: burst arrivals and a narrow worker pool
python -m lab.benchmark --traffic burst --rps 12 --concurrency 2 --output outputs/burst.json

# Unit tests plus a local mock HTTP provider (no paid calls)
python -m unittest discover -s tests -v
```

## What works

- Three policies: always strong, always economy, and a threshold-based adaptive router.
- Validation-only threshold selection, followed by held-out test evaluation.
- Minimum exact-match quality and p95 end-to-end latency constraints.
- Point estimates and 95% Wilson quality intervals, schema validity, field accuracy, and quality by difficulty.
- Token cost per 1,000 submitted requests, cost per correct response, tail latency, queueing, throughput, errors, and violation rate.
- Poisson arrivals and a 4× middle-of-run burst, with bounded client concurrency.
- Cost–quality chart with confidence intervals and latency failures, request-level prediction inspection, JSON/CSV export.
- Persistent SQLite history, background runs, reload recovery, input validation, and cross-origin POST rejection.
- Live calls to two configurable OpenAI-compatible endpoints. Provider usage, failures, and incomplete accounting remain visible.

## What the simulator actually proves

Simulation demonstrates the benchmark, routing, queueing, and decision machinery. **It does not measure an LLM or prove model cost savings.** All synthetic results are labeled in the UI and exports.

The fixtures contain `product`, `category`, and `urgency`. Quality is exact match across all three; invalid schemas fail. The simulator starts from expected labels and introduces seeded category errors. Strong-model correctness is 98.5%; economy correctness is 98%, 79%, or 47% for easy, medium, or hard text features. The routing heuristic intentionally aligns with the simulated difficulty: this is a controllable teaching model, not learned evidence.

The live prompt contains the same explicit category rubric used by the fixture generator: charges/invoices are billing, authentication is access, slowness is performance, and broken output or controls are bugs. Prompt versions are saved with results so prompt changes cannot be silently mixed in one comparison.

Illustrative input/output prices per million tokens are $0.15/$0.60 for economy and $2.50/$10.00 for strong. Token counts use character estimates. Service times use seeded lognormal variation and model-specific coefficients. A shared FIFO worker pool simulates client queueing. There is no real GPU scheduling, continuous batching, quantization, prefix cache, or TTFT measurement.

## Experiment protocol

1. Generate 120 validation fixtures and a separate test split using a saved seed.
2. Evaluate thresholds `0`, `0.2`, `0.6`, `1`. Route to economy when text difficulty is at most the threshold; otherwise strong.
3. Select the cheapest validation candidate with quality ≥ floor and p95 ≤ SLO. Complete cost accounting is required. If none qualifies, show strong-only as an explicitly unaccepted fallback.
4. Freeze the threshold. Run the three policies against the same held-out fixtures and arrival schedule.
5. Report acceptance only if validation found a feasible candidate and adaptive held-out performance passes both constraints with complete costs.
6. Save configuration, dataset hash, methodology version, all request records, candidate results, model metadata (never keys), and spending.

Eligibility uses point estimates, not the lower confidence bound. Test fixtures have distinct text references but share templates with validation, so the evaluation is not independent in the real-world generalization sense. The fixed policy order may bias live measurements under changing external load. The router is an interpretable heuristic, not a trained router or a continuously load-aware controller. Final latency is checked empirically; no SLO is guaranteed by routing.

## Connect real models

Use endpoints that accept `POST /v1/chat/completions`, JSON object output, `temperature`, and `max_tokens`, and return `usage.prompt_tokens` and `usage.completion_tokens`. Both endpoint URLs must include `/v1`. vLLM offers this API: [official server documentation](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/).

Set environment variables **before starting the server**. Replace the example model aliases with models served by your endpoints and prices with the applicable provider rates. `.env.example` is documentation; the app does not automatically load `.env` files.

```powershell
$env:ECONOMY_BASE_URL = 'http://127.0.0.1:8000/v1'
$env:ECONOMY_MODEL = 'your-economy-model'
$env:ECONOMY_INPUT_PER_MILLION = '0.15'
$env:ECONOMY_OUTPUT_PER_MILLION = '0.60'
# If your provider requires authentication:
# $env:ECONOMY_API_KEY = 'your-key'

$env:STRONG_BASE_URL = 'http://127.0.0.1:8001/v1'
$env:STRONG_MODEL = 'your-strong-model'
$env:STRONG_INPUT_PER_MILLION = '2.50'
$env:STRONG_OUTPUT_PER_MILLION = '10.00'
# $env:STRONG_API_KEY = 'your-key'

python -m lab.server
```

For local endpoints, omit all four `*_INPUT_PER_MILLION` and `*_OUTPUT_PER_MILLION` variables and set `ECONOMY_HOURLY_COST` and `STRONG_HOURLY_COST` to a positive GPU-hour rate. Cost is then measured request time × rate. Use concurrency 1 when interpreting this as occupied GPU time; concurrent request durations overlap and would otherwise double-count shared capacity. A value of `1.00` produces a transparent normalized dollar-per-GPU-hour comparison, not an electricity-bill claim.

Live mode makes **2 × validation requests + 3 × test requests**: 720 calls at the standard 120/160 configuration, with up to 150 output tokens requested per call. A 30-validation/20-test pilot uses 120 calls and has much wider statistical uncertainty. The UI requires deliberately selecting live mode. There are no automatic retries, cascades, or paid calls at startup. One benchmark job runs at a time. Do not shut down the server during a live run; partial jobs are not checkpointed, and provider charges may still occur.

Calibration calls each tier once per validation fixture. Candidate latency is estimated by replaying measured service times through the client worker model; it is not a direct live measurement of each candidate. The three final test policies use real timed calls including client queueing. The HTTP timeout is 45 seconds. A timed-out provider call may still be billed; missing usage is marked unknown, not represented as verified zero cost. Calibration spending is separate from the three-policy benchmark total. JSON exports contain both.

Prices describe token billing only. For self-hosted deployments, add GPU rental × elapsed provisioned time, idle capacity, and shared infrastructure attribution before making infrastructure savings claims. Setting prices to zero cannot establish cost savings.

## API

| Route | Purpose |
|---|---|
| `GET /api/health` | Engine status and live configuration readiness |
| `POST /api/runs` | Validate configuration and start a background run; returns job ID |
| `GET /api/jobs/{id}` | Poll job status |
| `GET /api/runs` | Latest 30 saved experiment summaries |
| `GET /api/runs/{id}` | Complete experiment JSON |
| `GET /api/runs/{id}/csv` | Request metrics for every policy |

Example POST body:

```json
{"mode":"simulation","requests":160,"seed":42,"rps":4,"concurrency":8,"quality_min":0.9,"latency_slo_ms":1800,"traffic":"steady"}
```

## Layout

```text
lab/dataset.py      Synthetic fixtures, text difficulty, exact-match scorer
lab/engine.py       Simulation, live adapter, load generator, policy calibration
lab/server.py       Local HTTP API, background jobs, SQLite persistence
lab/benchmark.py    Reproducible CLI benchmark
web/               Dependency-free responsive dashboard
tests/             Engine, validation, accounting, and mock provider tests
docs/              Architecture and next experiments
data/              Local database (ignored)
outputs/           Exported benchmark artifacts (ignored)
```

## Portfolio positioning

The defensible MVP claim is: **“Built a reproducible LLM cost–quality–latency workbench with validation-calibrated model routing, load tests, request-level cost accounting, and live endpoint support.”**

Only claim a measured percentage saving after real endpoint experiments. Include model versions, hardware/provider, date and prices, dataset provenance, quality confidence interval, request rate, concurrency, error rate, and SLO. Keep failed configurations in the report.

## Next experiments

1. Add independently labeled real tickets and domain-shift splits; retain a final untouched evaluation set.
2. Benchmark two actual models across several seeds and randomized policy order.
3. Add GPU-hour accounting and compare precision/quantization on the same device.
4. Measure batch-size and prefix-cache configurations; capture vLLM metrics for TTFT, decode, queueing, and utilization.
5. Replace the heuristic with a calibrated quality predictor, then evaluate load-aware routing and fallback costs.

Reference: [RouteLLM research](https://arxiv.org/abs/2406.18665), [vLLM metrics](https://docs.vllm.ai/en/latest/design/metrics/).

Local development application; no remote authentication, multi-user isolation, or production deployment hardening is included.

See [the local Ollama run guide](docs/local-live-run.md) and [verification log](docs/verification.md) for the first measured GPU pilot and its limitations.
