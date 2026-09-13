"""Validated, provider-neutral LLM trace records."""
from dataclasses import asdict, dataclass
from datetime import datetime
import math


OPTIONAL_NUMBERS = (
    "system_prompt_tokens", "retry_count", "rag_documents", "rag_documents_used",
    "rag_context_tokens", "complexity_score", "quality_label",
)


@dataclass(frozen=True)
class TraceRecord:
    trace_id: str
    timestamp: str
    application: str
    endpoint: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost: float
    currency: str = "USD"
    system_prompt_hash: str | None = None
    system_prompt_tokens: int | None = None
    error: str | None = None
    retry_count: int | None = None
    rag_documents: int | None = None
    rag_documents_used: int | None = None
    rag_context_tokens: int | None = None
    complexity_score: float | None = None
    quality_label: float | None = None

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Each trace must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown trace fields: {', '.join(sorted(unknown))}")
        required = ("trace_id", "timestamp", "application", "endpoint", "model", "prompt_version",
                    "input_tokens", "output_tokens", "latency_ms", "cost")
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"Missing trace fields: {', '.join(missing)}")
        for key in required[:6]:
            if not isinstance(value[key], str) or not value[key].strip():
                raise ValueError(f"{key} must be a non-empty string")
        try:
            datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be ISO-8601") from error
        for key in ("input_tokens", "output_tokens"):
            if type(value[key]) is not int or value[key] < 0:
                raise ValueError(f"{key} must be a non-negative integer")
        for key in ("latency_ms", "cost"):
            number = value[key]
            if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
                raise ValueError(f"{key} must be a non-negative finite number")
        for key in OPTIONAL_NUMBERS:
            number = value.get(key)
            if number is None:
                continue
            if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
                raise ValueError(f"{key} must be null or a non-negative finite number")
        for key in ("system_prompt_tokens", "retry_count", "rag_documents", "rag_documents_used", "rag_context_tokens"):
            if value.get(key) is not None and type(value[key]) is not int:
                raise ValueError(f"{key} must be an integer")
        for key in ("complexity_score", "quality_label"):
            if value.get(key) is not None and not 0 <= value[key] <= 1:
                raise ValueError(f"{key} must be between 0 and 1")
        if value.get("rag_documents_used") is not None and value.get("rag_documents") is not None:
            if value["rag_documents_used"] > value["rag_documents"]:
                raise ValueError("rag_documents_used cannot exceed rag_documents")
        return cls(**value)

    def to_dict(self):
        return asdict(self)


def normalize_records(records):
    if not isinstance(records, list) or not records:
        raise ValueError("records must be a non-empty array")
    parsed = [TraceRecord.parse(row) for row in records]
    ids = [row.trace_id for row in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate trace_id values are not allowed within a batch")
    currencies = {row.currency for row in parsed}
    if len(currencies) != 1:
        raise ValueError("A trace batch must use one currency")
    return parsed
