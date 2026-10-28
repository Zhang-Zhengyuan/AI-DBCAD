"""Durable cross-process API budget shared by research and the native AI editor."""

from __future__ import annotations
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import time


class BudgetExceeded(RuntimeError):
    pass


@contextmanager
def _locked(file):
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        if not file.read(1):
            file.write("0")
            file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


class Budget:
    """Reserve the full declared context + output before each provider request.

    Failures with unknown usage retain their reservation. File locking also
    prevents concurrent runs from sharing the same remaining money twice.
    """

    def __init__(self, path, limit, pricing):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.limit = float(limit)
        self.pricing = pricing
        if not math.isfinite(self.limit) or self.limit <= 0:
            raise ValueError("Budget must be finite and positive")
        for key in (
            "input_cny_per_million",
            "output_cny_per_million",
            "context_tokens",
        ):
            if not math.isfinite(pricing[key]) or pricing[key] < 0:
                raise ValueError("Pricing must be finite and nonnegative")
        if pricing["context_tokens"] < 1:
            raise ValueError("Context reservation must be positive")

    def update(self, action):
        with self.path.with_suffix(".lock").open("a+") as file, _locked(file):
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not data:
                data = {"limit_cny": self.limit, "pricing": self.pricing, "calls": []}
            if data["pricing"] != self.pricing or data["limit_cny"] != self.limit:
                raise ValueError("Budget terms changed; use a new ledger explicitly")
            value = action(data)
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            return value

    def reserve(self, model, max_output, label):
        p = self.pricing
        if model != p["model"]:
            raise ValueError("Model is not covered by the verified pricing")
        if max_output < 1 or max_output > p["context_tokens"]:
            raise ValueError("Output limit must be inside the priced context")
        cost = (
            p["context_tokens"] * p["input_cny_per_million"]
            + max_output * p["output_cny_per_million"]
        ) / 1e6

        def reserve(data):
            used = sum(c["charged_or_reserved_cny"] for c in data["calls"])
            if used + cost > self.limit:
                raise BudgetExceeded(
                    f"Next call reserves CNY {cost:.4f}; only {self.limit - used:.4f} remains"
                )
            index = len(data["calls"])
            data["calls"].append(
                {
                    "label": label,
                    "started": time.time(),
                    "status": "reserved",
                    "charged_or_reserved_cny": cost,
                    "reservation_cny": cost,
                }
            )
            return index

        return self.update(reserve)

    def settle(self, index, usage, response_model):
        p = self.pricing

        def settle(data):
            call = data["calls"][index]
            call["response_model"] = response_model
            if usage is None:
                call["status"] = "missing_usage_reserved"
                return
            payload = usage if isinstance(usage, dict) else usage.model_dump()
            prompt = int(payload["prompt_tokens"])
            completion = int(payload["completion_tokens"])
            if prompt < 0 or completion < 0:
                raise ValueError("Provider reported negative token usage")
            cost = (
                prompt * p["input_cny_per_million"]
                + completion * p["output_cny_per_million"]
            ) / 1e6
            call.update(
                {
                    "status": "completed",
                    "usage": payload,
                    "charged_or_reserved_cny": cost,
                    "estimated_cny": cost,
                }
            )
            return cost > call["reservation_cny"]

        if self.update(settle):
            raise BudgetExceeded(
                "Provider usage exceeded the reserved context/output; stopped"
            )

    def wait_for_slot(self, interval_seconds=2.0):
        """Smooth provider admission across processes; never sleep under a lock."""

        def slot(data):
            when = max(time.time(), data.get("next_request_at", 0))
            data["next_request_at"] = when + interval_seconds
            return when

        when = self.update(slot)
        time.sleep(max(0, when - time.time()))

    def summary(self):
        return self.update(
            lambda d: {
                "calls": len(d["calls"]),
                "estimated_cny": sum(c.get("estimated_cny", 0) for c in d["calls"]),
                "charged_or_reserved_cny": sum(
                    c["charged_or_reserved_cny"] for c in d["calls"]
                ),
                "limit_cny": d["limit_cny"],
            }
        )


class SharedBudget(Budget):
    """One authorization ledger, with immutable prices and provider sublimits.

    Existing single-model entries remain charged in place. No migration resets
    calls or reservations; untagged legacy entries belong to the default scope.
    All admission and settlement still use the same cross-process file lock.
    """

    def __init__(self, path, limit, pricing, *, scope="default", scope_limit=None):
        super().__init__(path, limit, pricing)
        self.scope = str(scope)
        self.scope_limit = None if scope_limit is None else float(scope_limit)
        if self.scope_limit is not None and (not math.isfinite(self.scope_limit) or self.scope_limit <= 0):
            raise ValueError("Provider budget must be finite and positive")

    def update(self, action):
        with self.path.with_suffix(".lock").open("a+") as file, _locked(file):
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not data:
                data = {"limit_cny": self.limit, "pricing": self.pricing, "calls": []}
            if data["limit_cny"] != self.limit:
                raise ValueError("Authorization limit changed; reconcile the existing ledger explicitly")
            # Keep the original pricing record for legacy calls and consumers.
            catalog = data.setdefault("pricing_catalog", {})
            if not catalog and data.get("calls"):
                catalog["default/" + data["pricing"]["model"]] = data["pricing"]
            key = self.scope + "/" + self.pricing["model"]
            if key in catalog and catalog[key] != self.pricing:
                raise ValueError("Model pricing changed; reconcile existing terms explicitly")
            catalog[key] = self.pricing
            value = action(data)
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            return value

    def reserve(self, model, max_output, label):
        p = self.pricing
        if model != p["model"] or not 1 <= max_output <= p["context_tokens"]:
            raise ValueError("Model/output is not covered by the verified pricing")
        cost = (p["context_tokens"] * p["input_cny_per_million"] +
                max_output * p["output_cny_per_million"]) / 1e6

        def reserve(data):
            used = sum(c["charged_or_reserved_cny"] for c in data["calls"])
            scoped = sum(c["charged_or_reserved_cny"] for c in data["calls"]
                         if c.get("scope", "default") == self.scope)
            if used + cost > self.limit:
                raise BudgetExceeded(f"Next call reserves CNY {cost:.4f}; only {self.limit-used:.4f} remains globally")
            if self.scope_limit is not None and scoped + cost > self.scope_limit:
                raise BudgetExceeded("Provider cumulative budget is exhausted across its models")
            index = len(data["calls"])
            data["calls"].append({"label": label, "started": time.time(), "status": "reserved",
                                  "scope": self.scope, "model": model,
                                  "pricing_key": self.scope + "/" + model,
                                  "reservation_cny": cost, "charged_or_reserved_cny": cost})
            return index

        return self.update(reserve)

    def summary(self):
        result = super().summary()
        result.update(scope=self.scope, scope_limit_cny=self.scope_limit)
        result["scope_charged_or_reserved_cny"] = self.update(lambda d: sum(
            c["charged_or_reserved_cny"] for c in d["calls"]
            if c.get("scope", "default") == self.scope))
        return result
