"""Offline regression checks for the money boundary, without provider requests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from provider import Budget, BudgetExceeded, is_budget_exceeded, load_credentials

PRICING = {'model': 'test-vision', 'context_tokens': 1000,
           'input_cny_per_million': 3, 'output_cny_per_million': 18}


class Usage(SimpleNamespace):
    def model_dump(self):
        return vars(self)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'ledger.json'

    def test_unknown_charge_stays_reserved_across_restart(self):
        budget = Budget(self.path, .01, PRICING)
        budget.reserve('test-vision', 100, 'failed-provider-call')
        budget = Budget(self.path, .01, PRICING)
        self.assertAlmostEqual(budget.summary()['charged_or_reserved_cny'], .0048)
        budget.reserve('test-vision', 100, 'second')
        with self.assertRaises(BudgetExceeded):
            budget.reserve('test-vision', 100, 'must-not-be-sent')
        self.assertEqual(budget.summary()['calls'], 2)

    def test_settlement_counts_completion_once_including_reasoning(self):
        budget = Budget(self.path, 1, PRICING)
        call = budget.reserve('test-vision', 100, 'ok')
        budget.settle(call, Usage(prompt_tokens=100, completion_tokens=20,
                                 completion_tokens_details={'reasoning_tokens': 10}), 'test-vision')
        self.assertAlmostEqual(budget.summary()['estimated_cny'], .00066)

    def test_changed_terms_and_wrong_models_refused(self):
        budget = Budget(self.path, 1, PRICING)
        budget.reserve('test-vision', 10, 'ok')
        with self.assertRaises(ValueError):
            Budget(self.path, 2, PRICING).summary()
        with self.assertRaises(ValueError):
            budget.reserve('unpriced-model', 10, 'must-not-be-sent')

    def test_env_file_is_data_and_not_executed(self):
        path = Path(self.directory.name) / 'provider.env'
        path.write_text('CAD_API_BASE_URL=https://example.invalid/v1\nCAD_API_KEY=$(never-execute)\n')
        values = load_credentials(path)
        self.assertEqual(values['CAD_API_KEY'], '$(never-execute)')

    def test_async_exception_group_preserves_budget_stop(self):
        error = ExceptionGroup('MCP context exit', [ExceptionGroup('nested', [BudgetExceeded('stop')])])
        self.assertTrue(is_budget_exceeded(error))
        self.assertFalse(is_budget_exceeded(ValueError('model error')))

    def test_provider_overrun_records_usage_before_stopping(self):
        budget = Budget(self.path, .01, PRICING)
        call = budget.reserve('test-vision', 100, 'provider-ignored-limit')
        with self.assertRaises(BudgetExceeded):
            budget.settle(call, Usage(prompt_tokens=1000, completion_tokens=1000), 'test-vision')
        self.assertAlmostEqual(budget.summary()['estimated_cny'], .021)
        with self.assertRaises(BudgetExceeded):
            budget.reserve('test-vision', 100, 'must-not-be-sent')


if __name__ == '__main__':
    unittest.main()
