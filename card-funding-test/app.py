#!/usr/bin/env python3
"""Sandbox card funding lab. No production endpoints or raw-card checkout."""
from __future__ import annotations

import argparse
import dataclasses
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
PRIVATE = Path('/var/tmp/dan-reap-kwal-test')
REAP = 'https://sg.sandbox.api.reap.global'
KWAL = 'https://api.sandbox.services.payward.com'
TEST_USDC = '0xFabab97dCE620294D2B0b0e46C68964e326300Ac'
OWNER_FILE = ROOT.parent / '.wallet/address.txt'
sys.path.insert(0, str(ROOT / 'vendor/kwal'))
import pws_client as kwal


def units(value: str, decimals: int = 6) -> int:
    try:
        scaled = Decimal(value) * 10 ** decimals
        if not scaled.is_finite() or scaled <= 0 or scaled != scaled.to_integral_value():
            raise ValueError('Amount must be positive with at most six decimal places.')
        result = int(scaled)
        if result >= 2 ** 64:
            raise ValueError('Amount exceeds the sandbox range.')
        return result
    except InvalidOperation:
        raise ValueError('Invalid amount.') from None


def private_env() -> dict[str, str]:
    path = PRIVATE / 'sandbox.env'
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Sandbox environment must be an owner-only regular file.')
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#'))


def session():
    credentials = kwal.CredentialStore(PRIVATE / 'kwal-credentials.json').load()
    if credentials.service_url != KWAL:
        raise ValueError('Refusing a session for another gateway.')
    if credentials.is_expired(int(time.time())):
        raise ValueError('Kwal session expired. Preserve it and request operator recovery.')
    return credentials


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def reap_get(route: str):
    env = private_env()
    if not env.get('REAP_API_KEY', '').startswith('sk_sg_sbx_'):
        raise ValueError('Only the Singapore sandbox key is accepted.')
    request = urllib.request.Request(REAP + route, headers={
        'Authorization': 'Bearer ' + env['REAP_API_KEY'],
        'Reap-Version': env.get('REAP_VERSION', '2025-02-14'),
        'Accept': 'application/json',
    })
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Provider error bodies can echo sensitive values. Report only status.
        raise ValueError(f'Reap read failed with HTTP {error.code}.') from None


def check():
    cards = reap_get('/cards/?limit=100')
    accounts = reap_get('/accounts/?limit=100')
    expected_last4 = private_env()['REAP_TEST_CARD_NUMBER'][-4:]
    return {
        'environment': 'sandbox', 'reap_authenticated': True,
        'visible_cards_first_page': len(cards['items']),
        'visible_accounts_first_page': len(accounts['items']),
        'more_cards': bool(cards.get('nextCursor')),
        'more_accounts': bool(accounts.get('nextCursor')),
        'supplied_card_last4_match_first_page': any(c.get('last4') == expected_last4 for c in cards['items']),
        'supplied_card_kwal_link': 'unverified',
        'card_credentials': 'private environment; not returned',
    }


def funding(required: int = 1_000_000):
    creds = session()
    result = kwal.read_funding(KWAL, creds.token, required_minor_units=str(required))
    return result


def rpc(method, params):
    request = urllib.request.Request('https://rpc-gel-sepolia.inkonchain.com',
        data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        body = json.load(response)
    if 'error' in body or 'result' not in body:
        raise ValueError('Ink Sepolia RPC failed.')
    return body['result']


def funding_plan(amount: int):
    if amount > 10_000_000:
        raise ValueError('This lab caps a planned test deposit at 10 USDC.')
    creds = session()
    setup = kwal.read_setup(KWAL, creds.token)
    owner = OWNER_FILE.read_text().strip()
    if not setup.owner_address or setup.owner_address.lower() != owner.lower():
        raise ValueError('Kwal setup is not bound to the saved test-wallet owner.')
    result = funding(amount)
    instructions = result.instructions
    if result.state in ('error', 'setup_needed', 'processing'):
        raise ValueError(f'Funding is {result.state}; preserve the same participant and resolve it first.')
    if not instructions or not result.vault_address or not result.available:
        raise ValueError('No complete verified funding instructions; ask the operator.')
    if (instructions.chain_id != 763373 or instructions.token_decimals != 6
            or instructions.token_address.lower() != TEST_USDC.lower()):
        raise ValueError('Refusing unexpected chain, token contract or precision.')
    if result.chain != 'eip155:763373' or setup.vault_address != result.vault_address:
        raise ValueError('Funding and setup vault/chain bindings disagree.')
    if int(rpc('eth_chainId', []), 16) != 763373:
        raise ValueError('RPC is on the wrong chain.')
    gas = int(rpc('eth_getBalance', [owner, 'latest']), 16)
    encoded = '0x70a08231' + owner[2:].lower().rjust(64, '0')
    token_balance = int(rpc('eth_call', [{'to': instructions.token_address, 'data': encoded}, 'latest']), 16)
    return {
        'environment': 'sandbox', 'broadcast': False,
        'owner': owner, 'vault': result.vault_address, 'chain_id': instructions.chain_id,
        'token': instructions.token_address, 'token_decimals': 6,
        'deposit_minor_units': amount, 'owner_token_minor_units': token_balance,
        'owner_gas_wei': gas,
        'wallet_can_cover_token_amount': token_balance >= amount,
        'wallet_has_gas': gas > 0,
        'service_card_spendable_minor_units': result.available.minor_units,
        'funding_state': result.state,
        'method': 'ERC20.transfer(vault, amount); no allowance required',
        'usdc_faucet': instructions.usdc_faucet_url,
        'gas_faucet': instructions.gas_faucet_url,
        'next': 'Fund this test wallet from the named faucets, then sign the exact transfer in the owner wallet. Recheck service readiness after receipt.',
    }


def fund(amount: int, send: bool, *, record_path=None, required_capacity=None):
    """One durable deposit intent; subsequent runs observe it, never replace it."""
    pending = Path(record_path) if record_path is not None else PRIVATE / 'deposit.json'
    if pending.parent != PRIVATE or pending.is_symlink():
        raise ValueError('Deposit records must remain in the private sandbox directory.')
    if pending.exists():
        info = pending.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Deposit record must be an owner-only regular file.')
        record = json.loads(pending.read_text())
        if record['amount_minor_units'] != amount:
            raise ValueError('A deposit already exists for another amount; reconcile it first.')
        receipt = rpc('eth_getTransactionReceipt', [record['transaction_hash']])
        return {'environment': 'sandbox', 'replayed': True, 'broadcast': False,
                'transaction_hash': record['transaction_hash'],
                'receipt_state': 'unknown_or_pending' if receipt is None else
                    ('confirmed' if int(receipt['status'], 16) == 1 else 'reverted'),
                'next': 'Check funding for service readiness. Do not make a replacement deposit.'}
    plan = funding_plan(amount)
    if not send:
        return plan
    if required_capacity is not None and (type(required_capacity) is not int or required_capacity <= 0):
        raise ValueError('Required capacity must be positive integer token units.')
    if plan['service_card_spendable_minor_units'] >= (required_capacity if required_capacity is not None else amount):
        return {'environment': 'sandbox', 'broadcast': False, 'state': 'already_sufficient'}
    if not plan['wallet_can_cover_token_amount'] or not plan['wallet_has_gas']:
        raise ValueError('Owner wallet lacks test USDC or gas. Claim the faucets; no transfer was signed or sent.')
    # Reuse the installed, audited web3 v8 environment and dedicated test key.
    try:
        from web3 import Web3
    except ImportError:
        raise ValueError('Run fund with ../.venv/bin/python; no new dependencies are needed.') from None
    sys.path.insert(0, str(ROOT.parent))
    import ink_wallet
    signer = ink_wallet.account()
    if signer.address.lower() != plan['owner'].lower():
        raise ValueError('Signer does not match the verified Kwal owner.')
    provider = Web3.HTTPProvider('https://rpc-gel-sepolia.inkonchain.com', request_kwargs={'timeout': 20})
    provider.global_ccip_read_enabled = False
    web3 = Web3(provider)
    if web3.eth.chain_id != 763373:
        raise ValueError('Refusing signing on a different chain.')
    data = '0xa9059cbb' + plan['vault'][2:].lower().rjust(64, '0') + hex(amount)[2:].rjust(64, '0')
    tx = {'chainId': 763373, 'from': signer.address, 'to': Web3.to_checksum_address(TEST_USDC),
          'value': 0, 'data': data, 'nonce': web3.eth.get_transaction_count(signer.address, 'pending'),
          'gasPrice': web3.eth.gas_price}
    tx['gas'] = web3.eth.estimate_gas(tx) * 12 // 10
    if tx['gas'] * tx['gasPrice'] > plan['owner_gas_wei']:
        raise ValueError('Insufficient test ETH for estimated gas; no transfer signed.')
    signed = signer.sign_transaction(tx)
    tx_hash = Web3.to_hex(signed.hash)
    record = {'transaction_hash': tx_hash, 'amount_minor_units': amount,
              'owner': signer.address, 'vault': plan['vault'], 'token': TEST_USDC,
              'chain_id': 763373, 'nonce': tx['nonce']}
    # O_EXCL prevents two processes from sending separate deposits. fsync precedes broadcast.
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(record, handle)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(PRIVATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    try:
        returned_hash = Web3.to_hex(web3.eth.send_raw_transaction(signed.raw_transaction))
    except Exception:
        raise ValueError(f'Deposit submission is uncertain. Preserve transaction {tx_hash}; rerun fund to observe it.') from None
    if returned_hash != tx_hash:
        raise ValueError('Unexpected broadcast hash; preserve deposit.json and reconcile.')
    return {'environment': 'sandbox', 'broadcast': True, 'transaction_hash': tx_hash,
            'state': 'submitted', 'next': 'Re-run fund to observe the receipt, then check service funding readiness.'}


class DemoLedger:
    """A synthetic issuer ledger, using exact units and atomic purchase writes."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS wallet(id INTEGER PRIMARY KEY CHECK(id=1), balance INTEGER NOT NULL CHECK(balance>=0));
            INSERT OR IGNORE INTO wallet VALUES(1,0);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL, amount INTEGER NOT NULL, merchant TEXT NOT NULL, status TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def apply(self, operation_id, kind, amount, merchant=''):
        if kind not in ('funding', 'purchase') or type(amount) is not int or amount <= 0:
            raise ValueError('Invalid ledger operation.')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            previous = self.db.execute('SELECT kind,amount,merchant,status FROM operations WHERE id=?', (operation_id,)).fetchone()
            replay = previous is not None
            if previous:
                if previous[:3] != (kind, amount, merchant):
                    raise ValueError('Operation ID already belongs to another request.')
                status = previous[3]
            else:
                balance = self.balance()
                status = 'declined' if kind == 'purchase' and balance < amount else 'completed'
                if status == 'completed':
                    delta = amount if kind == 'funding' else -amount
                    self.db.execute('UPDATE wallet SET balance=balance+? WHERE id=1', (delta,))
                self.db.execute('INSERT INTO operations VALUES(?,?,?,?,?)', (operation_id, kind, amount, merchant, status))
            balance = self.balance()
            self.db.execute('COMMIT')
            return {'id': operation_id, 'kind': kind, 'amount_minor_units': amount,
                    'merchant': merchant, 'status': status, 'replayed': replay,
                    'card_spendable_minor_units': balance}
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def balance(self):
        return self.db.execute('SELECT balance FROM wallet WHERE id=1').fetchone()[0]


def demo():
    with tempfile.TemporaryDirectory(prefix='card-funding-demo-') as directory:
        ledger = DemoLedger(Path(directory) / 'demo.sqlite')
        try:
            events = [
                ledger.apply('deposit-1', 'funding', 10_000_000),
                ledger.apply('purchase-1', 'purchase', 1_000_000, 'Test electronics shop'),
                ledger.apply('purchase-2', 'purchase', 2_000_000, 'Test supplies shop'),
                ledger.apply('purchase-2', 'purchase', 2_000_000, 'Test supplies shop'),
                ledger.apply('purchase-3', 'purchase', 8_000_000, 'Test over-budget shop'),
            ]
            return {'environment': 'offline_simulation', 'currency': 'TEST_USDC', 'decimals': 6,
                    'provider_calls': 0, 'real_merchant_orders': 0, 'events': events,
                    'final_card_spendable_minor_units': ledger.balance()}
        finally:
            ledger.close()


def helper(arguments):
    # Deliberately exclude raw calls, direct checkout and gateway overrides.
    allowed = {'register', 'setup', 'status', 'funding', 'products', 'product', 'variant',
               'quote', 'quote-check', 'shipping', 'payment', 'check', 'show'}
    if not arguments or arguments[0] not in allowed:
        raise ValueError('Unsupported helper command. Use purchase for bounded checkout.')
    if any(arg.startswith(('--credentials', '--service-url', '--force')) for arg in arguments):
        raise ValueError('Gateway and credential overrides are disabled in this lab.')
    if arguments[0] != 'register':
        session()
    env = os.environ.copy()
    env.update(PWS_SERVICE_URL=KWAL, PWS_CREDENTIALS_FILE=str(PRIVATE / 'kwal-credentials.json'))
    return subprocess.call([sys.executable, '-u', str(ROOT / 'vendor/kwal/register.py'), *arguments], env=env)


def purchase(quote_id, max_amount, *, on_payment=None):
    creds = session()
    store = kwal.AttemptStore(PRIVATE / 'kwal-credentials.json')
    previous = store.find(quote_id)
    if previous is not None:
        # An uncertain previous submission is observed, never replaced by a fresh ID.
        if on_payment:
            payment = kwal.read_payment(KWAL, creds.token, previous.payment_id, quote_id=quote_id)
            return _observe_payment(creds, payment, quote_id, on_payment)
        return helper(['payment', previous.payment_id])
    setup = kwal.read_setup(KWAL, creds.token)
    if setup.state != 'ready':
        raise ValueError(f'Setup is {setup.state} at {setup.step}; purchase not submitted.')
    quote = kwal.read_quote(KWAL, creds.token, quote_id)
    if quote.is_expired(int(time.time())) or quote.needs_shipping_selection():
        raise ValueError('Quote expired or shipping is not finalized.')
    readiness = kwal.read_funding(KWAL, creds.token, quote_id=quote_id)
    if readiness.state != 'ready' or readiness.required is None or readiness.quote is None:
        raise ValueError('Service has not confirmed funding for this quote; purchase not submitted.')
    if readiness.required.currency != 'USDC' or readiness.required.decimals != 6:
        raise ValueError('Unexpected quote funding currency or precision.')
    if (readiness.quote.token.chain_id != 763373 or
            readiness.quote.token.contract_address.lower() != TEST_USDC.lower()):
        raise ValueError('Quote funding does not use the verified test token and chain.')
    if readiness.quote.merchant_total != quote.total:
        raise ValueError('Quote changed during funding check; review its terms again.')
    if readiness.required.minor_units > max_amount:
        raise ValueError('Purchase exceeds the explicit test-USDC cap.')
    if kwal.read_quote(KWAL, creds.token, quote_id) != quote:
        raise ValueError('Quote changed before submission; review its terms again.')
    attempt = store.record(kwal.Attempt(payment_id=kwal.new_payment_id(), quote_id=quote_id))
    print(json.dumps({'environment': 'sandbox', 'payment_id': attempt.payment_id,
                      'quote_id': quote_id, 'max_usdc_minor_units': max_amount}), flush=True)
    payment = kwal.create_checkout(KWAL, creds.token, attempt.payment_id, quote_id)
    if on_payment:
        return _observe_payment(creds, payment, quote_id, on_payment)
    payment = kwal.poll_payment(KWAL, creds.token, payment, quote_id=quote_id)
    print(json.dumps({'environment': 'sandbox', 'real_merchant_order_proven': False,
                      'payment': dataclasses.asdict(payment)}, indent=2))
    return 0 if payment.state in ('completed', 'pending', 'requires_action') else 1


def _observe_payment(creds, payment, quote_id, callback):
    for index in range(11):
        callback(payment)
        if payment.state != 'pending' or index == 10:
            return payment
        time.sleep(3)
        payment = kwal.read_payment(KWAL, creds.token, payment.payment_id, quote_id=quote_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('demo')
    commands.add_parser('check')
    commands.add_parser('status')
    plan = commands.add_parser('funding-plan')
    plan.add_argument('--amount', default='5')
    transfer = commands.add_parser('fund')
    transfer.add_argument('--amount', default='5')
    transfer.add_argument('--send', action='store_true', help='sign and send one Ink Sepolia test-USDC deposit')
    funds = commands.add_parser('funding')
    funds.add_argument('--required', default='1')
    buy = commands.add_parser('purchase')
    buy.add_argument('quote_id')
    buy.add_argument('--max-usdc', required=True)
    raw = commands.add_parser('kwal')
    raw.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == 'demo':
            result = demo()
        elif args.command == 'check':
            result = check()
        elif args.command == 'status':
            creds = session()
            result = {'environment': 'sandbox', 'setup': dataclasses.asdict(kwal.read_setup(KWAL, creds.token))}
        elif args.command == 'funding':
            result = {'environment': 'sandbox', 'funding': dataclasses.asdict(funding(units(args.required)))}
        elif args.command == 'funding-plan':
            result = funding_plan(units(args.amount))
        elif args.command == 'fund':
            result = fund(units(args.amount), args.send)
        elif args.command == 'purchase':
            return purchase(args.quote_id, units(args.max_usdc))
        else:
            return helper(args.arguments)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, kwal.AgentPaymentError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
