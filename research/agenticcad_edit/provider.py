"""OpenAI-compatible transport with a durable CNY budget and no implicit retries."""
from __future__ import annotations

import json
import os
from pathlib import Path

import openai
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "CAD_DB/backend"))
from app.ai_budget import Budget, BudgetExceeded


def is_budget_exceeded(error):
    return isinstance(error, BudgetExceeded) or any(is_budget_exceeded(e) for e in getattr(error, 'exceptions', ()))


def load_credentials(path):
    # Parse data rather than sourcing arbitrary shell commands from an env file.
    values = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                values[k.strip()] = v.strip()
    return {k: os.environ.get(k, values.get(k, '')) for k in ['CAD_API_BASE_URL', 'CAD_API_KEY']}




class Transport:
    def __init__(self, credentials, budget, output, label):
        if not credentials['CAD_API_KEY']:
            raise ValueError('Set CAD_API_KEY in .local/provider.env or the environment')
        self.client = openai.OpenAI(api_key=credentials['CAD_API_KEY'],
                                   base_url=credentials['CAD_API_BASE_URL'],
                                   max_retries=0, timeout=180)
        self.budget = budget
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.label = label
        self.counter = 0

    def create(self, **kwargs):
        i = self.budget.reserve(kwargs['model'], kwargs['max_tokens'], self.label)
        prefix = self.output / f'call-{self.counter:03d}'
        self.counter += 1
        # Request bodies contain public benchmark assets; never store HTTP headers.
        Path(str(prefix) + '-request.json').write_text(json.dumps(kwargs, default=str))
        self.budget.wait_for_slot()
        response = self.client.chat.completions.create(**kwargs)
        self.budget.settle(i, response.usage, response.model)
        Path(str(prefix) + '-response.json').write_text(response.model_dump_json(indent=2))
        print(json.dumps({'api_call': self.label, 'call': self.counter, 'model': response.model,
                          'usage': response.usage.model_dump() if response.usage else None,
                          'budget': self.budget.summary()}), flush=True)
        return response
