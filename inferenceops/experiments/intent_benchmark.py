"""Real-model benchmark for the pinned Bitext customer-support intent task."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from lab.engine import cost_basis, provider_config
from .budget import BudgetLedger, preflight
from .fallback import FallbackConfig, evaluate_response_bank
from .task import SUPPORT_INTENT_TASK


SYSTEM_PROMPT = ("Classify the customer-support request. Return its category and intent from the supplied "
                 "enumerations, plus a confidence number between 0 and 1. Return only the JSON object.")


def _reservation(provider, input_tokens, output_tokens):
    return (input_tokens * float(provider["input_price"])
            + output_tokens * float(provider["output_price"])) / 1_000_000


def provider_observation(row, tier, ledger=None, input_reservation=600, max_output_tokens=80):
    provider = provider_config(tier)
    basis = cost_basis(provider)
    schema = SUPPORT_INTENT_TASK.schema(include_confidence=True)
    payload = {"model": provider["model"], "messages": [{"role": "system", "content": SYSTEM_PROMPT},
               {"role": "user", "content": row["text"]}], "temperature": 0,
               "max_tokens": max_output_tokens, "response_format": {"type": "json_schema",
               "json_schema": {"name": "support_intent", "strict": True, "schema": schema}}}
    if provider.get("reasoning_effort"):
        payload["reasoning_effort"] = provider["reasoning_effort"]
    headers = {"Content-Type": "application/json"}
    if provider.get("key"):
        headers["Authorization"] = f"Bearer {provider['key']}"
    reserved = _reservation(provider, input_reservation, max_output_tokens) if basis == "tokens" else 0
    if ledger and reserved:
        ledger.reserve(reserved)
    result = {"output": {}, "service_ms": 0, "input_tokens": 0, "output_tokens": 0,
              "cost": 0, "cost_known": False, "error": None}
    started = time.perf_counter()
    try:
        native_ollama = provider.get("ollama_think", "").lower() in ("true", "false")
        if native_ollama:
            payload = {"model": provider["model"], "messages": payload["messages"], "stream": False,
                       "think": provider["ollama_think"].lower() == "true", "format": schema,
                       "options": {"temperature": 0, "num_predict": max_output_tokens}}
            url = provider["base_url"].removesuffix("/v1") + "/api/chat"
        else:
            url = provider["base_url"] + "/chat/completions"
        request = urllib.request.Request(url,
                                         data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
        usage = (body.get("usage") or {"prompt_tokens": body.get("prompt_eval_count"),
                                       "completion_tokens": body.get("eval_count")})
        if all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("prompt_tokens", "completion_tokens")):
            result.update(input_tokens=usage["prompt_tokens"], output_tokens=usage["completion_tokens"])
            if basis == "tokens":
                result["cost"] = (usage["prompt_tokens"] * float(provider["input_price"])
                                  + usage["completion_tokens"] * float(provider["output_price"])) / 1_000_000
                result["cost_known"] = True
        try:
            content = (body["message"]["content"] if native_ollama
                       else body["choices"][0]["message"]["content"])
            result["output"] = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            result["error"] = "Invalid model JSON response"
    except urllib.error.HTTPError as error:
        result["error"] = f"Provider HTTP {error.code}"
    except Exception as error:
        result["error"] = f"Provider request failed ({type(error).__name__})"
    result["service_ms"] = (time.perf_counter() - started) * 1000
    if basis == "compute_time":
        result["cost"] = result["service_ms"] / 3_600_000 * float(provider["hourly_cost"])
        result["cost_known"] = True
    if ledger and reserved:
        ledger.settle(reserved, result["cost"] if result["cost_known"] else reserved)
    return result


def _load_manifest(path, limit=None):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return rows[:limit] if limit else rows


def run(validation, test, quality_min=.90, latency_slo_ms=5000, error_rate_max=.01,
        paid_confirmation="", budget_cap=5.0, budget_currency="USD", max_output_tokens=80):
    providers = {tier: provider_config(tier) for tier in ("economy", "strong")}
    for tier, provider in providers.items():
        if not provider["base_url"] or not provider["model"] or cost_basis(provider) is None:
            raise ValueError(f"{tier} provider is not completely configured")
    paid = any(provider["base_url"].startswith("https://") and cost_basis(provider) == "tokens"
               for provider in providers.values())
    check = None
    ledger = None
    if paid:
        priced = {tier: {"input_price": float(provider["input_price"]),
                         "output_price": float(provider["output_price"])} for tier, provider in providers.items()}
        check = preflight(len(validation), len(test), 600, max_output_tokens, priced,
                          budget_cap, budget_currency)
        if not check["within_cap"]:
            raise ValueError("Worst-case hosted benchmark cost exceeds the configured cap")
        required = f"RUN {budget_cap:.2f} {budget_currency}"
        if paid_confirmation != required:
            raise ValueError(f"Hosted benchmark requires confirmation: {required}")
        ledger = BudgetLedger(budget_cap, budget_currency)
    banks = {}
    for split, rows in (("validation", validation), ("test", test)):
        bank = {}
        for index, row in enumerate(rows, 1):
            bank[row["id"]] = {
                tier: provider_observation(row, tier, ledger,
                                           max_output_tokens=max_output_tokens)
                for tier in ("economy", "strong")
            }
            print(f"{split}: {index}/{len(rows)}", flush=True)
        banks[split] = bank
    result = evaluate_response_bank(validation, test, banks["validation"], banks["test"],
                                    SUPPORT_INTENT_TASK,
                                    FallbackConfig(quality_min, latency_slo_ms, error_rate_max))
    result.update({"dataset": {"name": SUPPORT_INTENT_TASK.name, "validation_count": len(validation),
                               "test_count": len(test), "label_status": "published dataset labels"},
                   "providers": {tier: {key: value for key, value in provider.items() if key != "key"}
                                 for tier, provider in providers.items()},
                   "preflight": check,
                   "max_output_tokens": max_output_tokens,
                   "budget": ({"cap": ledger.cap, "spent": ledger.spent, "remaining": ledger.remaining,
                               "currency": ledger.currency} if ledger else None)})
    return result


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Benchmark confidence fallback on the pinned Bitext dataset")
    parser.add_argument("--validation", type=int)
    parser.add_argument("--test", type=int)
    parser.add_argument("--quality-min", type=float, default=.90)
    parser.add_argument("--latency-slo-ms", type=float, default=5000)
    parser.add_argument("--error-rate-max", type=float, default=.01)
    parser.add_argument("--budget-cap", type=float, default=5.0)
    parser.add_argument("--budget-currency", default="USD")
    parser.add_argument("--max-output-tokens", type=int, default=80)
    parser.add_argument("--paid-confirmation", default="")
    parser.add_argument("--output", default="outputs/bitext-live-benchmark.json")
    args = parser.parse_args()
    external = root / "data" / "external"
    validation = _load_manifest(external / "bitext-validation.json", args.validation)
    test = _load_manifest(external / "bitext-test.json", args.test)
    result = run(validation, test, args.quality_min, args.latency_slo_ms, args.error_rate_max,
                 args.paid_confirmation, args.budget_cap, args.budget_currency,
                 args.max_output_tokens)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved {output}")


if __name__ == "__main__":
    main()
