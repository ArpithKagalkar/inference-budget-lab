"""Deterministic production-shaped traces for the portfolio demo."""
from datetime import datetime, timedelta, timezone
import hashlib
import random


def _cost(input_tokens, output_tokens, strong):
    prices = (2.5, 10.0) if strong else (0.15, 0.60)
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def generate_demo_traces(count=900, seed=42):
    if type(count) is not int or not 90 <= count <= 5000:
        raise ValueError("Demo trace count must be between 90 and 5000")
    rng = random.Random(seed)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    endpoints = ("/support/classify", "/support/respond", "/search/summarize")
    rows = []
    for index in range(count):
        endpoint = endpoints[index % len(endpoints)]
        if endpoint == "/support/classify":
            strong = rng.random() < .90
            system_tokens, input_tokens, output_tokens = 850, rng.randint(930, 1220), rng.randint(20, 55)
            complexity = rng.choices((.18, .45, .84), (.72, .20, .08))[0]
            rag_docs = rag_used = rag_tokens = None
            prompt_hash = "sha256:" + hashlib.sha256(b"support-classify-v7").hexdigest()
        elif endpoint == "/support/respond":
            strong = rng.random() < .70
            system_tokens, input_tokens, output_tokens = 1850, rng.randint(2100, 2800), rng.randint(120, 260)
            complexity = rng.uniform(.35, .85)
            rag_docs = rag_used = rag_tokens = None
            prompt_hash = "sha256:" + hashlib.sha256(b"support-response-v4").hexdigest()
        else:
            strong = rng.random() < .62
            system_tokens, input_tokens, output_tokens = 620, rng.randint(3100, 4700), rng.randint(100, 220)
            complexity = rng.uniform(.35, .90)
            rag_docs = rng.randint(11, 16)
            rag_used = rng.randint(2, 5)
            rag_tokens = rng.randint(2100, 3500)
            prompt_hash = "sha256:" + hashlib.sha256(b"search-summary-v3").hexdigest()
        latency = (rng.lognormvariate(6.45, .20) if strong else rng.lognormvariate(5.65, .18))
        timestamp = start + timedelta(seconds=index * 97)
        rows.append({
            "trace_id": f"demo-{seed}-{index:05d}", "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "application": "support-service", "endpoint": endpoint,
            "model": "strong" if strong else "economy", "prompt_version": prompt_hash[-10:],
            "system_prompt_hash": prompt_hash, "system_prompt_tokens": system_tokens,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "latency_ms": round(latency, 2), "cost": round(_cost(input_tokens, output_tokens, strong), 8),
            "currency": "USD", "error": None, "retry_count": 1 if rng.random() < .04 else 0,
            "rag_documents": rag_docs, "rag_documents_used": rag_used, "rag_context_tokens": rag_tokens,
            "complexity_score": round(complexity, 3), "quality_label": None,
        })
    return rows
