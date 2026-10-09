"""Standalone Pi item-chat worker. No wallet, Kwal session or financial-action imports."""
import fcntl
import json
import subprocess
import time
import chat_store as chat

NODE=chat.ROOT.parent/'durable-demo/.runtime/node_modules/.bin/node'
HELPER=chat.ROOT.parent/'durable-demo/scout-chat.mjs'


def reply(job):
    payload={'session_id':job['session_id'],'agent_id':job['agent_id'],
             'request_id':job['request_id'],'question':job['question'],'context':chat.context(job['agent_id'])}
    result=subprocess.run([str(NODE),str(HELPER)],input=json.dumps(payload),text=True,
                          capture_output=True,timeout=120)
    if result.returncode:raise ValueError('Agent reply unavailable')
    answer=json.loads(result.stdout.strip().splitlines()[-1])
    if not isinstance(answer.get('text'),str) or not answer['text'].strip():
        raise ValueError('Agent reply unavailable')
    chat.complete(job['request_id'],answer['text'],answer.get('model'))


if __name__=='__main__':
    chat.init()
    with (chat.DATA/'chat-worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with chat.db() as c:c.execute("UPDATE questions SET status='queued' WHERE status='running'")
        while True:
            job=chat.claim()
            if not job:time.sleep(.5);continue
            try:reply(job)
            except Exception:
                chat.complete(job['request_id'],'The agent could not reply right now. Please try the question again.',failed=True)
