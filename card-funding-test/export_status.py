"""Read-only Kwal observer. Export public demo facts, never credentials or card data."""
import concurrent.futures
import fcntl
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile

import app
from transport import request_json

OUTPUT = app.ROOT.parent / 'scout/data/card-status.json'


def chain_history(owner, vault, funding_receipt):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with (OUTPUT.parent / 'chain-history.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _chain_history(owner, vault, funding_receipt)


def _chain_history(owner, vault, funding_receipt):
    """Index this vault's ERC-20 transfers, with actual block times and a durable cursor."""
    path = OUTPUT.parent / 'chain-history.json'
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {'cursor': int(funding_receipt['blockNumber'], 16)-1 if funding_receipt else None,
                 'rows': [], 'updated_at': None}
    if cache['cursor'] is None:
        return [], True
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    address_topic = '0x'+vault[2:].lower().rjust(64, '0')
    failed = False
    try:
        latest = int(app.rpc('eth_blockNumber', []), 16)
        for _ in range(12):
            start = cache['cursor']+1
            if start > latest:
                break
            end = min(start+999, latest)
            logs = []
            for topics in ([topic, address_topic], [topic, None, address_topic]):
                logs.extend(app.rpc('eth_getLogs', [{'address': app.TEST_USDC,
                    'fromBlock': hex(start), 'toBlock': hex(end), 'topics': topics}]))
            seen = {(row['transaction_hash'], row['log_index']) for row in cache['rows']}
            for log in logs:
                key = (log['transactionHash'], int(log['logIndex'], 16))
                if key in seen or log.get('removed'):
                    continue
                seen.add(key)
                block = app.rpc('eth_getBlockByNumber', [log['blockNumber'], False])
                stamp = datetime.fromtimestamp(int(block['timestamp'], 16), timezone.utc).isoformat()
                from_address, to_address = '0x'+log['topics'][1][-40:], '0x'+log['topics'][2][-40:]
                row = {'transaction_hash': key[0], 'log_index': key[1],
                       'occurred_at': stamp, 'block': int(log['blockNumber'], 16),
                       'from': from_address, 'to': to_address,
                       'amount_test_usdc': format(Decimal(int(log['data'], 16))/1_000_000, '.6f'),
                       'direction': 'incoming' if to_address.lower() == vault.lower() else 'outgoing'}
                cache['rows'].append(row)
                import sys
                sys.path.insert(0, str(app.ROOT.parent/'scout'))
                import agent_flow
                agent_flow.init()
                agent_flow.event('chain:'+key[0]+':'+str(key[1]), 'Ink Sepolia', 'blockchain_transfer',
                    row['amount_test_usdc']+' test USDC '+('received by' if row['direction']=='incoming' else 'transferred from')+' the agent vault.',
                    source='blockchain', facts=row, occurred_at=stamp)
            cache['cursor'] = end
        cache['updated_at'] = datetime.now(timezone.utc).isoformat()
        save(cache, path=path)
        failed = cache['cursor'] < latest
    except Exception:
        failed = True
    return sorted(cache['rows'], key=lambda row: (row['block'], row['log_index'])), failed


def usdc(amount):
    if amount is None or amount.currency != 'USDC' or amount.decimals != 6:
        return None
    return format(Decimal(amount.minor_units) / 1_000_000, '.6f')


def payment_view(payment):
    if payment is None:
        return {'state': 'not_submitted', 'step': None, 'settled': False}
    # Do not publish approval links, provider reasons, emails or raw response objects.
    return {'state': payment.state, 'step': payment.step,
            'payment_id': payment.payment_id, 'quote_id': payment.quote_id,
            'card_transaction_id': payment.card_transaction_id,
            'charged_test_usdc': usdc(payment.charged), 'held_test_usdc': usdc(payment.held),
            'settled': (payment.state == 'completed' and payment.is_simulated_card_spend()
                        and usdc(payment.charged) is not None)}


def observe():
    creds = app.session()
    raw = request_json(app.KWAL, '/kwal/participant/v1/status', token=creds.token)
    setup = app.kwal.parse_setup(raw)
    owner = app.OWNER_FILE.read_text().strip()
    if not setup.owner_address or setup.owner_address.lower() != owner.lower():
        raise ValueError('Owner mismatch')
    funds = app.funding(1_000_000)
    vault = setup.vault_address
    instructions = funds.instructions
    if (setup.chain != 'eip155:763373' or not vault or funds.vault_address != vault
            or not instructions or instructions.chain_id != 763373
            or instructions.token_address.lower() != app.TEST_USDC.lower()):
        raise ValueError('Unexpected funding binding')
    if int(app.rpc('eth_chainId', []), 16) != 763373:
        raise ValueError('Wrong RPC chain')
    def balance(address):
        data = '0x70a08231' + address[2:].lower().rjust(64, '0')
        value = int(app.rpc('eth_call', [{'to': app.TEST_USDC, 'data': data}, 'latest']), 16)
        return format(Decimal(value) / 1_000_000, '.6f')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        owner_balance, vault_balance = pool.map(balance, [owner, vault])
    attempts = app.kwal.AttemptStore(app.PRIVATE / 'kwal-credentials.json').load()
    payment = (app.kwal.read_payment(app.KWAL, creds.token, attempts[-1].payment_id,
                                   quote_id=attempts[-1].quote_id) if attempts else None)
    payment_data = payment_view(payment)
    if payment is not None:
        payment_data['quote_id'] = attempts[-1].quote_id
        import sys
        sys.path.insert(0, str(app.ROOT.parent / 'scout'))
        import agent_flow
        agent_flow.init()
        agent_flow.record_payment(payment_data)
    deposit = {'state': 'not_submitted', 'amount_test_usdc': None, 'transaction_hash': None}
    record_path = app.PRIVATE / 'deposit.json'
    receipt = None
    if record_path.is_file():
        record = json.loads(record_path.read_text())
        if (record['owner'].lower() != owner.lower() or record['vault'].lower() != vault.lower()
                or record['token'].lower() != app.TEST_USDC.lower() or record['chain_id'] != 763373):
            raise ValueError('Deposit mismatch')
        tx = record['transaction_hash']
        receipt = app.rpc('eth_getTransactionReceipt', [tx])
        state = 'pending'
        if receipt:
            state = 'reverted' if int(receipt['status'], 16) != 1 else 'unverified'
            for log in receipt.get('logs', []):
                topics = log.get('topics', [])
                if (log['address'].lower() == app.TEST_USDC.lower() and len(topics) == 3
                        and topics[0] == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
                        and topics[1][-40:].lower() == owner[2:].lower()
                        and topics[2][-40:].lower() == vault[2:].lower()
                        and int(log['data'], 16) == record['amount_minor_units']
                        and int(receipt['status'], 16) == 1):
                    state = 'confirmed'
        deposit = {'state': state, 'transaction_hash': tx,
                   'amount_test_usdc': format(Decimal(record['amount_minor_units']) / 1_000_000, '.6f')}
    history, history_stale = chain_history(owner, vault, receipt)
    return {'schema_version': 1, 'mode': 'kwal_sandbox',
            'checked_at': datetime.now(timezone.utc).isoformat(), 'refresh_error': False,
            'card': {'status': setup.card_status, 'setup_state': setup.state,
                     'card_id': raw.get('reapCardId'), 'spendable_test_usdc': usdc(funds.available),
                     'funding_state': funds.state},
            'wallet': {'address': owner, 'balance_test_usdc': owner_balance},
            'vault': {'address': vault, 'balance_test_usdc': vault_balance},
            'deposit': deposit, 'payment': payment_data,
            'chain_history': history, 'chain_history_stale': history_stale}


def save(document, *, path=OUTPUT):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(document, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


if __name__ == '__main__':
    try:
        result = observe()
    except Exception:
        # Keep the last observed values and timestamp; mark them as unrefreshed.
        try:
            result = json.loads(OUTPUT.read_text())
        except (OSError, ValueError):
            result = {'schema_version': 1, 'mode': 'kwal_sandbox', 'checked_at': None}
        result['refresh_error'] = True
    save(result)
    print(json.dumps({'exported': True, 'refresh_error': result.get('refresh_error'),
                      'checked_at': result.get('checked_at')}))
