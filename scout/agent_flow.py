"""Persistent, bounded sandbox demo workflow. Public requests carry no payment parameters."""
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from card_monitor import read_status

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SCOUT_DATA_DIR', str(ROOT / 'data')))
DB = DATA / 'agent-flow.sqlite3'
AGENTS = [
    {'id': 'europe', 'name': 'Europe agent', 'market': 'Europe', 'live': True,
     'inventory': 'found', 'product': 'Café Kitsuné × Casio matcha watch',
     'product_id': 'matcha-watch', 'image': 'matcha-watch.png', 'retail_sgd': 240,
     'detail': 'Demo supplier inventory found. One unit available for this demo purchase.'},
    {'id': 'us', 'name': 'US agent', 'market': 'United States', 'live': False,
     'inventory': 'out_of_stock', 'product': 'Raspberry Pi 5 · 8 GB',
     'product_id': 'raspberry-pi-5', 'image': 'pi-board.svg', 'retail_sgd': 180,
     'detail': 'Demo supplier is out of stock. Funding and payment are unavailable.'},
    {'id': 'australia', 'name': 'Australia agent', 'market': 'Australia', 'live': False,
     'inventory': 'watching', 'product': 'Mechanical keyboard kit',
     'product_id': 'keyboard-kit', 'image': 'keyboard.svg', 'retail_sgd': 290,
     'detail': 'Watching for demo inventory. Card and vault have not been provisioned.'},
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    try:
        with c:
            yield c
    finally:
        c.close()


def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS flow_events(id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL,
          occurred_at TEXT NOT NULL, agent_id TEXT NOT NULL, actor TEXT NOT NULL, recipient TEXT,
          kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, facts TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cycles(cycle_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL, document TEXT NOT NULL, jobs TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT UNIQUE NOT NULL,
          status TEXT NOT NULL, requested_at TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0);
        ''')
        if 'attempt' not in {r['name'] for r in c.execute('PRAGMA table_info(jobs)')}:
            c.execute('ALTER TABLE jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0')
        c.execute('INSERT OR IGNORE INTO state VALUES(1,?)', (json.dumps({'cycle_id': 'demo-1',
                  'stage': 'discovering', 'quote': None, 'error': None}),))
        for agent in AGENTS:
            c.execute('INSERT OR IGNORE INTO flow_events(key,occurred_at,agent_id,actor,kind,text,source,facts) VALUES(?,?,?,?,?,?,?,?)',
                      ('inventory:'+agent['id'], now(), agent['id'], agent['name'], 'inventory',
                       agent['detail'], 'demo_inventory', '{}'))
        c.execute("INSERT OR IGNORE INTO jobs(action,status,requested_at) VALUES('prepare','queued',?)", (now(),))


def state():
    with db() as c:
        return json.loads(c.execute('SELECT document FROM state WHERE id=1').fetchone()[0])


def update(_cycle_id=None, **changes):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        value = json.loads(c.execute('SELECT document FROM state WHERE id=1').fetchone()[0])
        if _cycle_id is not None and value['cycle_id'] != _cycle_id:
            return False
        value.update(changes)
        c.execute('UPDATE state SET document=? WHERE id=1', (json.dumps(value),))
    return True


def event(key, actor, kind, text, *, recipient=None, source='controller', agent_id='europe', facts=None, occurred_at=None):
    with db() as c:
        c.execute('INSERT OR IGNORE INTO flow_events(key,occurred_at,agent_id,actor,recipient,kind,text,source,facts) VALUES(?,?,?,?,?,?,?,?,?)',
                  (key, occurred_at or now(), agent_id, actor, recipient, kind, text, source, json.dumps(facts or {})))


def enqueue(action, agent_id, cycle_id):
    if action not in ('prepare', 'fund', 'pay', 'repurchase') or agent_id != 'europe':
        raise ValueError('Only the live Europe agent can use sandbox payment controls.')
    init()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        value = json.loads(c.execute('SELECT document FROM state WHERE id=1').fetchone()[0])
        if cycle_id != value['cycle_id']:
            raise ValueError('The demo cycle changed. Refresh the page.')
        if action == 'repurchase':
            if value['stage'] != 'settled' or not value.get('payment', {}).get('settled'):
                raise ValueError('Wait for verified settlement before buying again.')
            if c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
                raise ValueError('The agent is finishing this purchase. Wait for its next update.')
            # Archive the completed purchase and its jobs atomically before preparing another.
            jobs = [dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY id')]
            c.execute('INSERT INTO cycles VALUES(?,?,?,?)',
                      (value['cycle_id'], now(), json.dumps(value), json.dumps(jobs)))
            next_cycle = 'demo-'+str(int(value['cycle_id'].split('-')[1])+1)
            fresh = {'cycle_id':next_cycle, 'stage':'discovering', 'quote':None, 'error':None}
            c.execute('DELETE FROM jobs')
            c.execute('UPDATE state SET document=? WHERE id=1', (json.dumps(fresh),))
            job_id = c.execute("INSERT INTO jobs(action,status,requested_at) VALUES('prepare','queued',?)", (now(),)).lastrowid
            c.execute('INSERT INTO flow_events(key,occurred_at,agent_id,actor,kind,text,source,facts) VALUES(?,?,?,?,?,?,?,?)',
                      (next_cycle+':started', now(), 'europe', 'Demo operator', 'cycle_started',
                       'Starting another demo purchase. A fresh quote will be checked; no funds or payment submitted.',
                       'controller', json.dumps({'cycle_id':next_cycle, 'previous_cycle_id':value['cycle_id']})))
            return {'accepted':True, 'replayed':False, 'job_id':job_id, 'cycle_id':next_cycle}
        prior = c.execute('SELECT * FROM jobs WHERE action=?', (action,)).fetchone()
        checking_payment = action == 'pay' and value['stage'] != 'settled' and bool(value.get('payment', {}).get('payment_id'))
        if prior and (prior['status'] in ('queued', 'running') or prior['status'] == 'done' and not checking_payment):
            return {'accepted': True, 'replayed': True, 'job_id': prior['id']}
        if c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
            raise ValueError('The agent is still working. Wait for its next update.')
        if action in ('fund', 'pay') and not value.get('quote'):
            raise ValueError('The fixed demo item needs a verified quote first.')
        checking_funding = action == 'fund' and value['stage'] == 'reconcile' and not value.get('payment', {}).get('payment_id')
        if value['stage'] in ('settled', 'submitted', 'authorizing', 'settling', 'reconcile') and action != 'pay' and not checking_funding:
            raise ValueError('Keep the existing payment; a new funding or quote action is unavailable.')
        if action == 'pay' and value['stage'] not in ('ready', 'reconcile', 'submitted', 'authorizing', 'settling'):
            raise ValueError('The card must be funded before payment.')
        if action == 'fund' and value['stage'] not in ('needs_funding', 'ready') and not checking_funding:
            raise ValueError('Funding is unavailable at this stage.')
        if prior:
            c.execute("UPDATE jobs SET status='queued',requested_at=? WHERE id=?", (now(), prior['id']))
            job_id = prior['id']
        else:
            job_id = c.execute("INSERT INTO jobs(action,status,requested_at) VALUES(?,'queued',?)", (action, now())).lastrowid
        return {'accepted': True, 'replayed': False, 'job_id': job_id}


def claim():
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row:
            c.execute("UPDATE jobs SET status='running',attempt=attempt+1 WHERE id=?", (row['id'],))
            return dict(row) | {'attempt': row['attempt']+1}


def finish(job_id, status='done'):
    with db() as c:
        c.execute('UPDATE jobs SET status=? WHERE id=?', (status, job_id))


def record_payment(value):
    current = state()
    selected = current.get('quote') or {}
    if not value.get('payment_id') or value.get('quote_id') != selected.get('quote_id'):
        return
    with db() as c:
        if not c.execute("SELECT 1 FROM jobs WHERE action='pay'").fetchone():
            return
    if current.get('payment', {}).get('payment_id') not in (None, value['payment_id']):
        return
    if current['stage'] == 'settled' and not value.get('settled'):
        return # Never let an older read undo confirmed settlement.
    expected = selected.get('price_test_usdc','1.000000')
    settled = value.get('settled') and value.get('charged_test_usdc') == expected
    if settled:
        stage, text = 'settled', 'Sandbox card payment settled. The vault paid '+expected+' test USDC.'
    elif value['state'] == 'completed':
        stage, text = 'reconcile', 'Payment completed; settlement of the approved amount is not yet verified.'
    elif value['state'] in ('declined', 'needs_operator', 'requires_action'):
        stage, text = 'reconcile', 'The sandbox payment needs attention. Keep this payment ID.'
    elif value.get('step') == 'card_clearing':
        stage, text = 'settling', 'Card clearing in progress. Waiting for vault settlement.'
    elif value.get('card_transaction_id') and value.get('held_test_usdc') is not None:
        stage, text = 'authorizing', 'Card authorization recorded. Test USDC is held for payment.'
    else:
        stage, text = 'submitted', 'Payment accepted by Kwal. Waiting for the card authorization worker.'
    if not update(_cycle_id=current['cycle_id'], stage=stage, payment=value, error=None):
        return
    facts = {key: value.get(key) for key in ('payment_id','card_transaction_id','charged_test_usdc','held_test_usdc')}
    event('payment:'+value['payment_id']+':'+stage, 'Kwal', 'payment_'+stage, text, source='kwal_api', facts=facts)
    if settled:
        import inventory
        with inventory.transaction() as stock:
            receipts = stock.setdefault('sandbox_stock_receipts', [])
            if not any(r['payment_id'] == value['payment_id'] for r in receipts):
                next(p for p in stock['products'] if p['id'] == 'matcha-watch')['stock'] += 1
                receipts.append({'payment_id':value['payment_id'],'product_id':'matcha-watch',
                                 'quantity':1,'created':now(),'mode':'sandbox_substitute'})
        event('listed:'+value['payment_id'], 'Europe agent', 'demo_listing',
              'Added one matcha watch to the demo storefront inventory after sandbox settlement.',
              source='demo_inventory', facts={'payment_id':value['payment_id'],'quantity':1,
                                             'substitute':selected.get('title')})


def public_state():
    init()
    current = state()
    card = read_status(DATA / 'card-status.json')
    with db() as c:
        events = []
        for row in c.execute('SELECT * FROM flow_events ORDER BY occurred_at ASC,id ASC'):
            item = dict(row);item.pop('key');item['facts'] = json.loads(item['facts']);events.append(item)
        jobs = [dict(row) for row in c.execute('SELECT id,action,status,requested_at FROM jobs ORDER BY id')]
    return {'agents': AGENTS, 'workflow': current, 'events': events, 'jobs': jobs,
            'card_demo': card, 'server_time': now(), 'mode': 'sandbox',
            'purchase_cap_test_usdc': 10, 'pi_runtime': 'Pi Durable / Pi AI 1.1.0'}
