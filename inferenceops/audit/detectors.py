"""Explainable, deterministic AI spend opportunity detectors."""
from collections import Counter, defaultdict
import hashlib
import json


def _opportunity(kind, endpoint, title, confidence, saving, currency, eligible, evidence, limitations):
    identifier = hashlib.sha256(f"{kind}:{endpoint}".encode()).hexdigest()[:12]
    return {
        "id": identifier, "type": kind, "endpoint": endpoint, "title": title,
        "status": "DISCOVERED", "confidence": confidence,
        "estimated_savings": round(max(0, saving), 6), "currency": currency,
        "eligible_for_experiment": eligible, "evidence": evidence,
        "limitations": limitations,
    }


def audit_traces(traces):
    groups = defaultdict(list)
    for trace in traces:
        row = trace.to_dict() if hasattr(trace, "to_dict") else trace
        groups[row["endpoint"]].append(row)
    findings = []
    for endpoint, rows in sorted(groups.items()):
        currency = rows[0].get("currency", "USD")
        total_cost = sum(row["cost"] for row in rows)
        strong = [row for row in rows if row["model"].lower() == "strong"]
        complexities = [row["complexity_score"] for row in rows if row.get("complexity_score") is not None]
        low_share = sum(value <= .35 for value in complexities) / len(complexities) if complexities else None
        strong_share = len(strong) / len(rows)
        if len(rows) >= 20 and strong_share >= .70 and low_share is not None and low_share >= .60:
            findings.append(_opportunity(
                "oversized_model", endpoint, "Premium model handles mostly low-complexity traffic", "high",
                sum(row["cost"] for row in strong) * .65, currency, True,
                {"requests": len(rows), "observed_cost": round(total_cost, 6),
                 "strong_model_share": round(strong_share, 4), "low_complexity_share": round(low_share, 4)},
                ["Savings are an estimate until replayed against labelled requests.",
                 "Complexity scores must come from an independently validated signal in production."],
            ))
        prompt_rows = [row for row in rows if row.get("system_prompt_hash") and row.get("system_prompt_tokens")]
        if len(prompt_rows) >= 20:
            common_hash, common_count = Counter(row["system_prompt_hash"] for row in prompt_rows).most_common(1)[0]
            common = [row for row in prompt_rows if row["system_prompt_hash"] == common_hash]
            repeated_tokens = sum(row["system_prompt_tokens"] for row in common[1:])
            repeated_share = common_count / len(prompt_rows)
            avg_prompt_ratio = sum(row["system_prompt_tokens"] / max(1, row["input_tokens"]) for row in common) / len(common)
            if repeated_share >= .80 and repeated_tokens >= 10_000:
                findings.append(_opportunity(
                    "repeated_prompt", endpoint, "Repeated prompt context dominates input usage", "medium",
                    total_cost * avg_prompt_ratio * .50, currency, False,
                    {"requests": len(rows), "observed_cost": round(total_cost, 6),
                     "common_prompt_share": round(repeated_share, 4), "repeated_prompt_tokens": repeated_tokens,
                     "prompt_input_ratio": round(avg_prompt_ratio, 4)},
                    ["This is an upper-bound opportunity, not verified savings.",
                     "Actual caching discounts and cache eligibility depend on the provider."],
                ))
        rag_rows = [row for row in rows if row.get("rag_documents") is not None and row.get("rag_documents_used") is not None]
        if len(rag_rows) >= 20:
            avg_docs = sum(row["rag_documents"] for row in rag_rows) / len(rag_rows)
            utilization = sum(row["rag_documents_used"] for row in rag_rows) / max(1, sum(row["rag_documents"] for row in rag_rows))
            rag_ratio = sum((row.get("rag_context_tokens") or 0) / max(1, row["input_tokens"]) for row in rag_rows) / len(rag_rows)
            if avg_docs >= 8 and utilization <= .50:
                findings.append(_opportunity(
                    "excessive_rag", endpoint, "Retrieved context has low document utilization", "medium",
                    total_cost * rag_ratio * (1 - utilization) * .50, currency, False,
                    {"requests": len(rows), "observed_cost": round(total_cost, 6),
                     "average_documents": round(avg_docs, 2), "document_utilization": round(utilization, 4),
                     "rag_input_ratio": round(rag_ratio, 4)},
                    ["Citation or usage markers are only a proxy for retrieval value.",
                     "Reducing retrieval depth requires a separate answer-quality evaluation."],
                ))
    return sorted(findings, key=lambda row: row["estimated_savings"], reverse=True)
