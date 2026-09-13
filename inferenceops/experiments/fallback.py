"""Validation-selected economy-first fallback evaluation."""
from dataclasses import dataclass
from lab.engine import percentile, wilson


@dataclass(frozen=True)
class FallbackConfig:
    quality_min: float = .90
    latency_slo_ms: float = 5000
    error_rate_max: float = .01
    thresholds: tuple[float, ...] = (.50, .65, .80, .90)


def _usable_economy(observation, threshold, task):
    output = observation.get("output") or {}
    confidence = output.get("confidence")
    return (observation.get("error") is None and task.score(output, output)["valid"]
            and type(confidence) in (int, float) and 0 <= confidence <= 1 and confidence >= threshold)


def _policy_record(row, bank, policy, threshold, task):
    economy, strong = bank[row["id"]]["economy"], bank[row["id"]]["strong"]
    if policy == "strong":
        selected, calls, model = strong, [strong], "strong"
    elif policy == "economy":
        selected, calls, model = economy, [economy], "economy"
    elif _usable_economy(economy, threshold, task):
        selected, calls, model = economy, [economy], "economy"
    else:
        selected, calls, model = strong, [economy, strong], "strong"
    grading = task.score(selected.get("output"), row["expected"])
    return {
        "id": row["id"], "text": row["text"], "expected": row["expected"],
        "output": selected.get("output") or {}, "model": model, "fallback": len(calls) == 2,
        "latency_ms": sum(call.get("service_ms", 0) for call in calls),
        "cost": sum(call.get("cost", 0) for call in calls),
        "cost_known": all(call.get("cost_known", False) for call in calls),
        "input_tokens": sum(call.get("input_tokens", 0) for call in calls),
        "output_tokens": sum(call.get("output_tokens", 0) for call in calls),
        "error": selected.get("error"), **grading,
    }


def _summarize(records, cfg):
    n = len(records)
    correct = sum(row["correct"] for row in records)
    latencies = [row["latency_ms"] for row in records]
    total_cost = sum(row["cost"] for row in records)
    known = all(row["cost_known"] for row in records)
    quality, p95 = correct / n, percentile(latencies, .95)
    error_rate = sum(bool(row["error"]) for row in records) / n
    return {
        "requests": n, "quality": quality, "quality_ci": wilson(correct, n),
        "schema_validity": sum(row["valid"] for row in records) / n,
        "p50_ms": percentile(latencies, .50), "p95_ms": p95, "p99_ms": percentile(latencies, .99),
        "total_cost": total_cost, "cost_per_1k": total_cost / n * 1000,
        "cost_known": known, "error_rate": error_rate,
        "fallback_rate": sum(row["fallback"] for row in records) / n,
        "feasible": known and quality >= cfg.quality_min and p95 <= cfg.latency_slo_ms
                    and error_rate <= cfg.error_rate_max,
    }


def _evaluate(rows, bank, policy, threshold, task, cfg):
    records = [_policy_record(row, bank, policy, threshold, task) for row in rows]
    return _summarize(records, cfg), records


def evaluate_response_bank(validation, test, validation_bank, test_bank, task, config=None):
    cfg = config or FallbackConfig()
    candidates = []
    for threshold in cfg.thresholds:
        metrics, _ = _evaluate(validation, validation_bank, "adaptive", threshold, task, cfg)
        candidates.append({"threshold": threshold, **metrics})
    eligible = [row for row in candidates if row["feasible"]]
    selected = min(eligible, key=lambda row: row["cost_per_1k"]) if eligible else candidates[0]
    results = []
    for policy, name in (("strong", "Always strong"), ("economy", "Always economy"),
                         ("adaptive", "Economy with confidence fallback")):
        metrics, records = _evaluate(test, test_bank, policy, selected["threshold"], task, cfg)
        results.append({"id": policy, "name": name, "metrics": metrics, "records": records})
    baseline = results[0]["metrics"]
    for result in results:
        metrics = result["metrics"]
        metrics["savings"] = (1 - metrics["cost_per_1k"] / baseline["cost_per_1k"]
                              if metrics["cost_known"] and baseline["cost_known"] and baseline["cost_per_1k"] else None)
    adaptive = results[2]["metrics"]
    accepted = bool(eligible) and adaptive["feasible"] and adaptive["cost_per_1k"] < baseline["cost_per_1k"]
    return {"task": task.name, "threshold": selected["threshold"], "calibration": candidates,
            "results": results, "validation_feasible": bool(eligible), "accepted": accepted,
            "methodology_version": "inferenceops-fallback-1.0"}
