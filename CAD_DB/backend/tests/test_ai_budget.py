"""Parallel consumers cannot reserve the same remaining API money twice."""

from concurrent.futures import ThreadPoolExecutor
from app.ai_budget import Budget, BudgetExceeded


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
