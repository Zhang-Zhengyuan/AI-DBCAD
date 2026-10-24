"""OpenAI-compatible transport with a durable CNY budget and no implicit retries."""
from __future__ import annotations

import fcntl
import json
import math
import os
from pathlib import Path
import time

import openai


class BudgetExceeded(RuntimeError):
    pass


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
            raise ValueError('Budget must be finite and positive')
        for key in ('input_cny_per_million', 'output_cny_per_million', 'context_tokens'):
            if not math.isfinite(pricing[key]) or pricing[key] < 0:
                raise ValueError('Pricing must be finite and nonnegative')
        if pricing['context_tokens'] < 1:
            raise ValueError('Context reservation must be positive')

    def update(self, action):
        with self.path.with_suffix('.lock').open('a+') as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not data:
                data = {'limit_cny': self.limit, 'pricing': self.pricing, 'calls': []}
            if data['pricing'] != self.pricing or data['limit_cny'] != self.limit:
                raise ValueError('Budget terms changed; use a new ledger explicitly')
            value = action(data)
            temporary = self.path.with_suffix('.tmp')
            with temporary.open('w') as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
            return value

    def reserve(self, model, max_output, label):
        p = self.pricing
        if model != p['model']:
            raise ValueError('Model is not covered by the verified pricing')
        if max_output < 1 or max_output > p['context_tokens']:
            raise ValueError('Output limit must be inside the priced context')
        cost = (p['context_tokens'] * p['input_cny_per_million'] +
                max_output * p['output_cny_per_million']) / 1e6

        def reserve(data):
            used = sum(c['charged_or_reserved_cny'] for c in data['calls'])
            if used + cost > self.limit:
                raise BudgetExceeded(f'Next call reserves CNY {cost:.4f}; only {self.limit-used:.4f} remains')
            index = len(data['calls'])
            data['calls'].append({'label': label, 'started': time.time(), 'status': 'reserved',
                                  'charged_or_reserved_cny': cost, 'reservation_cny': cost})
            return index
        return self.update(reserve)

    def settle(self, index, usage, response_model):
        p = self.pricing
        def settle(data):
            call = data['calls'][index]
            call['response_model'] = response_model
            if usage is None:
                call['status'] = 'missing_usage_reserved'
                return
            prompt = int(usage.prompt_tokens)
            completion = int(usage.completion_tokens)
            if prompt < 0 or completion < 0:
                raise ValueError('Provider reported negative token usage')
            cost = (prompt * p['input_cny_per_million'] + completion * p['output_cny_per_million']) / 1e6
            call.update({'status': 'completed', 'usage': usage.model_dump(),
                         'charged_or_reserved_cny': cost, 'estimated_cny': cost})
            return cost > call['reservation_cny']
        if self.update(settle):
            raise BudgetExceeded('Provider usage exceeded the reserved context/output; stopped')

    def summary(self):
        return self.update(lambda d: {'calls': len(d['calls']),
                           'estimated_cny': sum(c.get('estimated_cny', 0) for c in d['calls']),
                           'charged_or_reserved_cny': sum(c['charged_or_reserved_cny'] for c in d['calls']),
                           'limit_cny': d['limit_cny']})


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
        response = self.client.chat.completions.create(**kwargs)
        self.budget.settle(i, response.usage, response.model)
        Path(str(prefix) + '-response.json').write_text(response.model_dump_json(indent=2))
        print(json.dumps({'api_call': self.label, 'call': self.counter, 'model': response.model,
                          'usage': response.usage.model_dump() if response.usage else None,
                          'budget': self.budget.summary()}), flush=True)
        return response
