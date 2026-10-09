import concurrent.futures
from pathlib import Path
import tempfile
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

try:
    from eth_account import Account
    from web3 import Web3
except ImportError:
    Account = None

import app


class FundingTests(unittest.TestCase):
    def test_repeat_purchase_does_not_charge_twice(self):
        ledger = app.DemoLedger(':memory:')
        self.addCleanup(ledger.close)
        ledger.apply('fund', 'funding', 10_000_000)
        ledger.apply('order', 'purchase', 3_000_000, 'shop')
        self.assertTrue(ledger.apply('order', 'purchase', 3_000_000, 'shop')['replayed'])
        self.assertEqual(ledger.balance(), 7_000_000)
        with self.assertRaises(ValueError):
            ledger.apply('order', 'purchase', 4_000_000, 'shop')
        self.assertEqual(ledger.balance(), 7_000_000)

    def test_concurrent_purchases_cannot_overspend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.sqlite'
            ledger = app.DemoLedger(path)
            ledger.apply('fund', 'funding', 10_000_000)
            ledger.close()
            def purchase(i):
                db = app.DemoLedger(path)
                try:
                    return db.apply(f'order-{i}', 'purchase', 7_000_000, 'shop')['status']
                finally:
                    db.close()
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                results = list(pool.map(purchase, range(2)))
            self.assertEqual(sorted(results), ['completed', 'declined'])
            ledger = app.DemoLedger(path)
            self.assertEqual(ledger.balance(), 3_000_000)
            ledger.close()

    def test_exact_usdc_units(self):
        self.assertEqual(app.units('1.000001'), 1_000_001)
        for value in ['0', '-1', 'NaN', 'Infinity', '1.0000001', 'bad']:
            with self.assertRaises(ValueError):
                app.units(value)

    def test_fixture_is_explicitly_offline(self):
        result = app.demo()
        self.assertEqual(result['environment'], 'offline_simulation')
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['final_card_spendable_minor_units'], 7_000_000)

    def test_no_raw_checkout_or_gateway_override(self):
        for args in [['call', '/anything'], ['checkout', 'quote'], ['register', '--service-url=https://example.com']]:
            with self.assertRaises(ValueError):
                app.helper(args)

    def test_unready_setup_never_submits_purchase(self):
        setup = type('Setup', (), {'state': 'pending', 'step': 'deposit_observation'})()
        creds = type('Session', (), {'token': 'synthetic-token'})()
        with patch.object(app, 'session', return_value=creds), patch.object(app.kwal, 'read_setup', return_value=setup), patch.object(app.subprocess, 'call') as submit:
            with self.assertRaises(ValueError):
                app.purchase('quote', 1_000_000)
            submit.assert_not_called()

    def test_missing_wallet_funds_never_sign_or_send(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, 'PRIVATE', Path(directory)), patch.object(app, 'funding_plan', return_value={
            'service_card_spendable_minor_units': 0,
            'wallet_can_cover_token_amount': False, 'wallet_has_gas': False,
        }):
            with self.assertRaisesRegex(ValueError, 'no transfer was signed or sent'):
                app.fund(5_000_000, True)
            self.assertFalse((Path(directory) / 'deposit.json').exists())

    def test_deposit_recovery_observes_without_replacing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deposit.json'
            path.write_text(json.dumps({'amount_minor_units': 5_000_000, 'transaction_hash': '0x' + 'a' * 64}))
            path.chmod(0o600)
            with patch.object(app, 'PRIVATE', Path(directory)), patch.object(app, 'rpc', return_value=None) as rpc, patch.object(app, 'funding_plan') as plan:
                result = app.fund(5_000_000, True)
                self.assertTrue(result['replayed'])
                self.assertFalse(result['broadcast'])
                self.assertEqual(result['receipt_state'], 'unknown_or_pending')
                plan.assert_not_called()
                rpc.assert_called_once()
                with self.assertRaises(ValueError):
                    app.fund(6_000_000, True)

    def test_purchase_cap_and_changed_quote_stop_submission(self):
        creds = SimpleNamespace(token='synthetic-token')
        quote = SimpleNamespace(total=app.kwal.Amount(100, 'USD', 2),
            is_expired=lambda now: False, needs_shipping_selection=lambda: False)
        readiness = SimpleNamespace(state='ready', required=app.kwal.Amount(1_000_000, 'USDC', 6),
            quote=SimpleNamespace(merchant_total=quote.total,
                token=SimpleNamespace(chain_id=763373, contract_address=app.TEST_USDC)))
        with patch.object(app, 'session', return_value=creds), patch.object(app.kwal, 'AttemptStore') as store, patch.object(app.kwal, 'read_setup', return_value=SimpleNamespace(state='ready')), patch.object(app.kwal, 'read_quote', return_value=quote) as read, patch.object(app.kwal, 'read_funding', return_value=readiness), patch.object(app.kwal, 'create_checkout') as submit:
            store.return_value.find.return_value = None
            with self.assertRaisesRegex(ValueError, 'cap'):
                app.purchase('quote', 500_000)
            submit.assert_not_called()
            store.return_value.record.assert_not_called()
            read.side_effect = [quote, SimpleNamespace(total=app.kwal.Amount(200, 'USD', 2))]
            with self.assertRaisesRegex(ValueError, 'changed'):
                app.purchase('quote', 1_000_000)
            submit.assert_not_called()
            store.return_value.record.assert_not_called()

    def test_uncertain_previous_purchase_is_only_observed(self):
        with patch.object(app, 'session'), patch.object(app.kwal, 'AttemptStore') as store, patch.object(app, 'helper', return_value=0) as helper, patch.object(app.kwal, 'create_checkout') as submit:
            store.return_value.find.return_value = SimpleNamespace(payment_id='saved-payment')
            app.purchase('quote', 1_000_000)
            helper.assert_called_once_with(['payment', 'saved-payment'])
            submit.assert_not_called()


@unittest.skipIf(Account is None, 'Signing tests use the existing ../.venv interpreter')
class SigningTests(unittest.TestCase):
    def test_deposit_is_saved_before_broadcast_and_recovery_cannot_resend(self):
        signer = Account.create()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = SimpleNamespace(global_ccip_read_enabled=True)
            eth = MagicMock()
            eth.chain_id = 763373
            eth.gas_price = 1
            eth.get_transaction_count.return_value = 0
            eth.estimate_gas.return_value = 50_000
            plan = {'service_card_spendable_minor_units': 0, 'wallet_can_cover_token_amount': True,
                    'wallet_has_gas': True, 'owner': signer.address, 'owner_gas_wei': 1_000_000,
                    'vault': '0x' + 'b' * 40}
            def broadcast(raw):
                self.assertTrue((root / 'deposit.json').exists())
                record = json.loads((root / 'deposit.json').read_text())
                self.assertEqual(record['transaction_hash'], Web3.to_hex(Web3.keccak(raw)))
                self.assertEqual(Account.recover_transaction(raw), signer.address)
                return Web3.keccak(raw)
            eth.send_raw_transaction.side_effect = broadcast
            with patch.object(app, 'PRIVATE', root), patch.object(app, 'funding_plan', return_value=plan), patch.dict('sys.modules', {'ink_wallet': SimpleNamespace(account=lambda: signer)}), patch('web3.Web3') as factory:
                factory.HTTPProvider.return_value = provider
                factory.return_value = SimpleNamespace(eth=eth)
                factory.to_hex.side_effect = Web3.to_hex
                factory.to_checksum_address.side_effect = Web3.to_checksum_address
                self.assertTrue(app.fund(5_000_000, True)['broadcast'])
                with patch.object(app, 'rpc', return_value={'status': '0x1'}):
                    replay = app.fund(5_000_000, True)
                    self.assertFalse(replay['broadcast'])
                    self.assertEqual(replay['receipt_state'], 'confirmed')
                eth.send_raw_transaction.assert_called_once()


if __name__ == '__main__':
    unittest.main()
