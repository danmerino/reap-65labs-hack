import json
from types import SimpleNamespace
import unittest
from export_status import payment_view


class PaymentProjectionTests(unittest.TestCase):
    def payment(self, **changes):
        fields = dict(state='pending', step='card_authorization', payment_id='payment-1',
                      quote_id='quote-1', card_transaction_id=None, charged=None, held=None,
                      approval_url='https://private.example/secret', reason='private provider text',
                      is_simulated_card_spend=lambda: True)
        return SimpleNamespace(**(fields | changes))

    def test_unsubmitted_is_not_a_success(self):
        self.assertEqual(payment_view(None)['state'], 'not_submitted')
        self.assertFalse(payment_view(None)['settled'])

    def test_completed_order_without_card_settlement_is_not_settled(self):
        amount = SimpleNamespace(currency='USDC', decimals=6, minor_units=1_000_000)
        self.assertFalse(payment_view(self.payment(state='completed', charged=amount,
                                                  is_simulated_card_spend=lambda: False))['settled'])
        self.assertFalse(payment_view(self.payment(state='completed'))['settled'])
        self.assertTrue(payment_view(self.payment(state='completed', charged=amount))['settled'])

    def test_sensitive_provider_fields_are_never_exported(self):
        text = json.dumps(payment_view(self.payment()))
        self.assertNotIn('secret', text)
        self.assertNotIn('private provider', text)
        self.assertNotIn('approval_url', text)


if __name__ == '__main__':
    unittest.main()
