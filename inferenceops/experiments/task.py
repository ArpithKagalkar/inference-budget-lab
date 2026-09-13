"""Reusable exact-match task contracts."""
from dataclasses import dataclass


BITEXT_CATEGORIES = (
    "ACCOUNT", "CANCEL", "CONTACT", "DELIVERY", "FEEDBACK", "INVOICE",
    "ORDER", "PAYMENT", "REFUND", "SHIPPING", "SUBSCRIPTION",
)
BITEXT_INTENTS = (
    "cancel_order", "change_order", "change_shipping_address", "check_cancellation_fee",
    "check_invoice", "check_payment_methods", "check_refund_policy", "complaint",
    "contact_customer_service", "contact_human_agent", "create_account", "delete_account",
    "delivery_options", "delivery_period", "edit_account", "get_invoice", "get_refund",
    "newsletter_subscription", "payment_issue", "place_order", "recover_password",
    "registration_problems", "review", "set_up_shipping_address", "switch_account",
    "track_order", "track_refund",
)


@dataclass(frozen=True)
class TaskSpec:
    name: str
    output_fields: tuple[str, ...]
    allowed_values: dict[str, tuple[str, ...]]

    def score(self, output, expected):
        if not isinstance(output, dict):
            return {"correct": False, "valid": False,
                    "fields": {field: False for field in self.output_fields}}
        answer = {key: value for key, value in output.items() if key != "confidence"}
        valid = set(answer) == set(self.output_fields)
        if valid:
            valid = all(answer[field] in self.allowed_values[field] for field in self.output_fields)
        fields = {field: bool(valid and answer.get(field) == expected.get(field)) for field in self.output_fields}
        return {"correct": all(fields.values()), "valid": bool(valid), "fields": fields}

    def schema(self, include_confidence=False):
        properties = {field: {"type": "string", "enum": list(self.allowed_values[field])}
                      for field in self.output_fields}
        required = list(self.output_fields)
        if include_confidence:
            properties["confidence"] = {"type": "number", "minimum": 0, "maximum": 1}
            required.append("confidence")
        return {"type": "object", "properties": properties, "required": required,
                "additionalProperties": False}


SUPPORT_INTENT_TASK = TaskSpec(
    name="bitext-customer-support-intent-v1",
    output_fields=("category", "intent"),
    allowed_values={"category": BITEXT_CATEGORIES, "intent": BITEXT_INTENTS},
)
