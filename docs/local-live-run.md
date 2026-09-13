# Running the local Ollama experiment

The current machine is configured for:

- Economy: `qwen3:0.6b`
- Strong: `qwen3:4b`
- Endpoint: Ollama at `http://127.0.0.1:11434/v1`
- Cost basis: measured response time at a normalized `$1.00` per occupied GPU-hour
- Reasoning disabled for both models so hidden thinking tokens do not dominate a small extraction task

Both models are installed. Start the live-configured dashboard from the repository root:

```powershell
.\start-ollama-lab.ps1
```

For the Bitext intent benchmark, enable Ollama's native structured-output path explicitly:

```powershell
$env:ECONOMY_BASE_URL='http://127.0.0.1:11434/v1'
$env:ECONOMY_MODEL='qwen3:0.6b'
$env:ECONOMY_HOURLY_COST='1'
$env:ECONOMY_OLLAMA_THINK='false'
$env:STRONG_BASE_URL='http://127.0.0.1:11434/v1'
$env:STRONG_MODEL='qwen3:4b'
$env:STRONG_HOURLY_COST='1'
$env:STRONG_OLLAMA_THINK='false'
python -m inferenceops.experiments.intent_benchmark --validation 10 --test 10 --max-output-tokens 80
```

The `*_OLLAMA_THINK` settings are only sent to Ollama's native API. Hosted OpenAI-compatible providers continue to receive the standard structured-output request.

Open `http://127.0.0.1:8787`, choose **Live endpoints**, and begin with:

- Validation requests: 30
- Test requests: 20
- Workers: 1
- Arrival rate: 1.5 requests/second
- Quality: 90%
- p95 latency: 5,000 ms

This creates 120 model calls. The live-mode selector shows the exact count before launch. Use one worker when treating elapsed inference time as occupied GPU time, because concurrent request durations can overlap.

The first corrected-prompt pilot did not meet the constraints. That is a valid benchmark result. To reach the 90% quality floor, use a stronger model or refine the task and evaluation contract based on independent domain examples. To reach the 5-second SLO, measure warmup separately, reduce model size/precision, improve serving, or loosen the operating point. Do not lower a requirement merely to manufacture a passing result.

Before a portfolio claim, replace generated tickets with an independently labeled dataset, use at least a few hundred untouched test examples, repeat runs, report confidence intervals, and replace the normalized GPU-hour rate with an actual cloud rental or attributed infrastructure rate.
