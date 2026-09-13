"""Conservative provider-spend preflight and reservation."""
from dataclasses import dataclass
import math
import threading
from dataclasses import field


def _valid_price(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def preflight(validation_count, test_count, input_tokens, max_output_tokens, providers, cap, currency="USD"):
    if any(type(value) is not int or value <= 0 for value in
           (validation_count, test_count, input_tokens, max_output_tokens)):
        raise ValueError("Counts and token estimates must be positive integers")
    if not _valid_price(cap) or cap <= 0:
        raise ValueError("Budget cap must be positive")
    calls_per_tier = validation_count + test_count
    tiers = {}
    total = 0
    for tier in ("economy", "strong"):
        provider = providers[tier]
        if not _valid_price(provider.get("input_price")) or not _valid_price(provider.get("output_price")):
            raise ValueError(f"{tier} token prices must be configured")
        per_call = (input_tokens * provider["input_price"] + max_output_tokens * provider["output_price"]) / 1_000_000
        tier_cost = calls_per_tier * per_call
        tiers[tier] = {"calls": calls_per_tier, "reserved_per_call": per_call, "maximum_cost": tier_cost}
        total += tier_cost
    return {"currency": currency, "call_count": calls_per_tier * 2, "tiers": tiers,
            "maximum_cost": total, "cap": cap, "within_cap": total <= cap,
            "requires_confirmation": True}


@dataclass
class BudgetLedger:
    cap: float
    currency: str = "USD"
    reserved: float = 0
    spent: float = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self, amount):
        if not _valid_price(amount):
            raise ValueError("Reservation must be a non-negative finite number")
        with self._lock:
            if self.reserved + self.spent + amount > self.cap + 1e-12:
                raise RuntimeError("Paid-run budget exhausted before scheduling provider request")
            self.reserved += amount

    def settle(self, reserved, actual):
        with self._lock:
            if not _valid_price(reserved) or not _valid_price(actual) or reserved > self.reserved + 1e-12:
                raise ValueError("Invalid budget settlement")
            self.reserved -= reserved
            self.spent += actual

    @property
    def remaining(self):
        with self._lock:
            return max(0, self.cap - self.reserved - self.spent)
