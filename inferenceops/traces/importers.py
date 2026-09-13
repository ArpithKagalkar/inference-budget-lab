"""File import helpers for normalized and exported trace data."""
import csv
import json
from pathlib import Path
from .model import normalize_records


INTEGER_FIELDS = {"input_tokens", "output_tokens", "system_prompt_tokens", "retry_count",
                  "rag_documents", "rag_documents_used", "rag_context_tokens"}
FLOAT_FIELDS = {"latency_ms", "cost", "complexity_score", "quality_label"}


def load_file(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        records = value.get("records", value) if isinstance(value, dict) else value
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
        for row in records:
            for field in INTEGER_FIELDS:
                if row.get(field) not in (None, ""):
                    row[field] = int(row[field])
                elif field in row:
                    row[field] = None
            for field in FLOAT_FIELDS:
                if row.get(field) not in (None, ""):
                    row[field] = float(row[field])
                elif field in row:
                    row[field] = None
            if row.get("error") == "":
                row["error"] = None
    else:
        raise ValueError("Trace files must be JSON or CSV")
    return normalize_records(records)


def normalize_langfuse_export(records, application="imported-langfuse-app"):
    """Normalize the stable subset expected from a staged Langfuse JSON export."""
    normalized = []
    for item in records:
        usage = item.get("usage") or item.get("usage_details") or {}
        metadata = item.get("metadata") or {}
        normalized.append({
            "trace_id": str(item.get("id") or item.get("trace_id") or ""),
            "timestamp": item.get("timestamp") or item.get("start_time") or "",
            "application": metadata.get("application", application),
            "endpoint": metadata.get("endpoint", item.get("name", "unknown")),
            "model": item.get("model") or metadata.get("model") or "unknown",
            "prompt_version": metadata.get("prompt_version", "unknown"),
            "system_prompt_hash": metadata.get("system_prompt_hash"),
            "system_prompt_tokens": metadata.get("system_prompt_tokens"),
            "input_tokens": usage.get("input", usage.get("prompt_tokens", 0)),
            "output_tokens": usage.get("output", usage.get("completion_tokens", 0)),
            "latency_ms": item.get("latency_ms", metadata.get("latency_ms", 0)),
            "cost": item.get("cost", item.get("calculated_total_cost", 0)),
            "currency": metadata.get("currency", "USD"), "error": item.get("error"),
            "retry_count": metadata.get("retry_count"), "rag_documents": metadata.get("rag_documents"),
            "rag_documents_used": metadata.get("rag_documents_used"),
            "rag_context_tokens": metadata.get("rag_context_tokens"),
            "complexity_score": metadata.get("complexity_score"), "quality_label": metadata.get("quality_label"),
        })
    return normalize_records(normalized)
