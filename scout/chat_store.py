"""Item discussion only: isolated anonymous threads and a read-only agent dossier."""
import json
import os
from pathlib import Path
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from card_monitor import read_status

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('SCOUT_DATA_DIR',str(ROOT/'data')))
DB=DATA/'agent-chat.sqlite3'
DOSSIER=ROOT/'agent-dossiers.json'
AGENTS=('europe','us','australia')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def identifier(value):
    if not isinstance(value,str):raise ValueError('Invalid chat identifier')
    try:return str(uuid.UUID(value))
    except ValueError:raise ValueError('Invalid chat identifier') from None


def thread(session_id,agent_id):
    if agent_id not in AGENTS:raise ValueError('Unknown agent')
    return identifier(session_id),agent_id


@contextmanager
def db():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=10);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    try:
        with c:yield c
    finally:c.close()


def init():
    with db() as c:c.execute('''CREATE TABLE IF NOT EXISTS questions(
       id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL,
       agent_id TEXT NOT NULL, question TEXT NOT NULL, created_at TEXT NOT NULL,
       status TEXT NOT NULL, answer TEXT, answered_at TEXT, model TEXT)''')


def submit(session_id,agent_id,request_id,message):
    session_id,agent_id=thread(session_id,agent_id);request_id=identifier(request_id)
    if not isinstance(message,str) or not 1<=len(message.strip())<=2000:
        raise ValueError('Enter a message of 1–2000 characters')
    message=message.strip();init()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        previous=c.execute('SELECT * FROM questions WHERE request_id=?',(request_id,)).fetchone()
        if previous:
            if (previous['session_id'],previous['agent_id'],previous['question'])!=(session_id,agent_id,message):
                raise ValueError('That request ID belongs to another message')
            return {'accepted':True,'request_id':request_id,'replayed':True}
        if c.execute("SELECT 1 FROM questions WHERE session_id=? AND agent_id=? AND status IN ('queued','running')",(session_id,agent_id)).fetchone():
            raise ValueError('This agent is replying. Wait for the answer before sending another message.')
        if c.execute('SELECT COUNT(*) FROM questions WHERE session_id=? AND agent_id=?',(session_id,agent_id)).fetchone()[0]>=40:
            raise ValueError('This demo conversation has reached its message limit.')
        threshold=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        if c.execute('SELECT COUNT(*) FROM questions WHERE created_at>?',(threshold,)).fetchone()[0]>=20:
            raise ValueError('The demo is busy. Please try again shortly.')
        c.execute("INSERT INTO questions(request_id,session_id,agent_id,question,created_at,status) VALUES(?,?,?,?,?,'queued')",(request_id,session_id,agent_id,message,now()))
    return {'accepted':True,'request_id':request_id,'replayed':False}


def claim():
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM questions WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row:
            c.execute("UPDATE questions SET status='running' WHERE id=?",(row['id'],))
            return dict(row)


def complete(request_id,answer,model=None,failed=False):
    with db() as c:c.execute('UPDATE questions SET answer=?,answered_at=?,model=?,status=? WHERE request_id=?',
        (answer[:14000],now(),model,'failed' if failed else 'done',request_id))


def context(agent_id):
    if agent_id not in AGENTS:raise ValueError('Unknown agent')
    dossier=json.loads(DOSSIER.read_text())
    profile=dossier['agents'][agent_id]
    products={'matcha-watch':dossier['products']['matcha-watch']}
    primary=profile['primary_product'];products[primary]=dossier['products'][primary]
    live={'inventory_status':profile['inventory_status'],'real_goods_shipped':False,
          'chat_can_transact':False,'financial_mode':profile['financial_mode']}
    card=read_status(DATA/'card-status.json')
    if agent_id=='europe':
        live.update(card_status=card.get('card',{}).get('status'),
            card_spendable_test_usdc=card.get('card',{}).get('spendable_test_usdc'),
            payment=card.get('payment',{}),observation_is_stale=card.get('stale',True))
    return {'agent':profile,'knowledge_rules':dossier['knowledge_rules'],
            'products':products,'live_context':live,'dossier_updated_on':dossier['updated_on']}


def read(session_id,agent_id):
    session_id,agent_id=thread(session_id,agent_id);init()
    with db() as c:rows=list(c.execute('SELECT * FROM questions WHERE session_id=? AND agent_id=? ORDER BY id',(session_id,agent_id)))
    messages=[]
    for row in rows:
        messages.append({'id':row['request_id']+':user','role':'user','text':row['question'],'created_at':row['created_at']})
        if row['answer'] is not None:
            messages.append({'id':row['request_id']+':agent','role':'assistant','text':row['answer'],
                             'created_at':row['answered_at'],'model':row['model'],'failed':row['status']=='failed'})
    profile=json.loads(DOSSIER.read_text())['agents'][agent_id]
    return {'agent_id':agent_id,'agent_name':profile['name'],'messages':messages,
            'busy':any(row['status'] in ('queued','running') for row in rows),
            'suggested_questions':profile['suggested_questions'],'dossier_words':len(json.dumps(json.loads(DOSSIER.read_text())['products']['matcha-watch']).split())}
