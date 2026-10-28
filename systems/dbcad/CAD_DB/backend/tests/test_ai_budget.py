"""Parallel consumers cannot reserve the same remaining API money twice."""

from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from app.ai_budget import Budget, SharedBudget, BudgetExceeded


def test_parallel_reservations_share_one_durable_limit(tmp_path):
    path = tmp_path / "budget.json"
    pricing = {
        "model": "fake",
        "context_tokens": 1000,
        "input_cny_per_million": 3,
        "output_cny_per_million": 18,
    }

    def reserve(index):
        try:
            return Budget(path, 0.01, pricing).reserve("fake", 100, str(index))
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [
            index for index in pool.map(reserve, range(16)) if index is not None
        ]
    assert sorted(accepted) == [0, 1]
    result = Budget(path, 0.01, pricing).summary()
    assert result["calls"] == 2
    assert result["charged_or_reserved_cny"] <= result["limit_cny"]


def test_accounting_only_preserves_ledger_and_crosses_global_and_provider_caps(tmp_path):
    path = tmp_path / "budget.json"
    pricing = dict(model="fake", context_tokens=1000,
                   input_cny_per_million=3, output_cny_per_million=18)
    limited = SharedBudget(path, .001, pricing, scope_limit=.001)
    with pytest.raises(BudgetExceeded):
        limited.reserve("fake", 100, "blocked-before-change")
    # Keep an uncertain legacy request exactly as recorded.
    data = json.loads(path.read_text()) if path.exists() else {
        "limit_cny": .001, "pricing": pricing, "calls": []}
    old = {"label": "legacy-unknown", "status": "reserved", "charged_or_reserved_cny": 100}
    data["calls"].append(old)
    path.write_text(json.dumps(data))
    unlimited = SharedBudget(path, .001, pricing, scope_limit=.001, enforce=False)
    index = unlimited.reserve("fake", 100, "new-job")
    # Usage beyond even the reservation is recorded without a money stop.
    unlimited.settle(index, {"prompt_tokens": 10000, "completion_tokens": 1000}, "fake")
    summary = unlimited.summary()
    assert summary["limit_cny"] is None and summary["enforced"] is False
    assert summary["scope_limit_cny"] is None
    assert summary["charged_or_reserved_cny"] == pytest.approx(100.048)
    saved = json.loads(path.read_text())
    assert saved["limit_cny"] == .001 and saved["calls"][0] == old
    assert saved["calls"][1]["status"] == "completed"
    # Switching back to the old policy still enforces the original terms.
    with pytest.raises(BudgetExceeded):
        limited.reserve("fake", 100, "limited-again")


def test_accounting_only_keeps_unknown_usage_reserved(tmp_path):
    pricing = dict(model="fake", context_tokens=1000,
                   input_cny_per_million=3, output_cny_per_million=18)
    budget = SharedBudget(tmp_path / "budget.json", .001, pricing, enforce=False)
    index = budget.reserve("fake", 100, "unknown")
    budget.settle(index, None, None)
    saved = json.loads(budget.path.read_text())["calls"][index]
    assert saved["status"] == "missing_usage_reserved"
    assert budget.summary()["charged_or_reserved_cny"] == saved["reservation_cny"]
