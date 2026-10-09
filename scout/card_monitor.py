"""Serve only a whitelist of the sandbox observer's public facts."""
import json
from datetime import datetime, timezone

FIELDS = {
    'card': ('status', 'setup_state', 'card_id', 'spendable_test_usdc', 'funding_state'),
    'wallet': ('address', 'balance_test_usdc'),
    'vault': ('address', 'balance_test_usdc'),
    'deposit': ('state', 'amount_test_usdc', 'transaction_hash'),
    'payment': ('state', 'step', 'payment_id', 'quote_id', 'card_transaction_id',
                'charged_test_usdc', 'held_test_usdc', 'settled'),
}


def read_status(path):
    try:
        raw = json.loads(path.read_text())
        if raw.get('schema_version') != 1 or raw.get('mode') != 'kwal_sandbox':
            raise ValueError('Unexpected observer schema')
        stamp = raw.get('checked_at')
        fresh = (stamp is not None and
                 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() < 60)
        result = {'mode': 'kwal_sandbox', 'checked_at': stamp,
                  'stale': not fresh or bool(raw.get('refresh_error')), 'available': stamp is not None}
        for section, keys in FIELDS.items():
            source = raw.get(section, {})
            result[section] = {key: source[key] for key in keys
                               if key in source and isinstance(source[key], (str, bool, type(None)))}
        result['chain_history'] = [{key: row[key] for key in
            ('transaction_hash','occurred_at','block','log_index','from','to','amount_test_usdc','direction')
            if key in row and isinstance(row[key], (str,int))} for row in raw.get('chain_history', [])[-200:]
            if isinstance(row, dict)]
        result['chain_history_stale'] = bool(raw.get('chain_history_stale', True))
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return {'mode': 'kwal_sandbox', 'available': False, 'stale': True, 'checked_at': None}
