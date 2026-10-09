"""Five-minute demo inventory agents. No supplier or payment requests."""
import argparse
import fcntl
import json
from catalog import SOURCES
from store import DATA, connect, init, now
from inventory import manage_stock

def scan(reason="cron"):
    init()
    with (DATA/"scan.lock").open("a") as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {"status":"already_running"}
        with connect() as c:
            c.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'",(now(),))
            run=c.execute("INSERT INTO runs(started,reason,status) VALUES(?,?,'running')",(now(),reason)).lastrowid
            enabled={r["id"] for r in c.execute("SELECT id FROM agents WHERE enabled=1")}
        try:
            results=manage_stock([s["id"] for s in SOURCES if s["id"] in enabled],run)
            for source in SOURCES:
                if source["id"] not in results: continue
                result=results[source["id"]];timestamp=now()
                with connect() as c:
                    c.execute("INSERT INTO observations(run_id,agent_id,checked,result) VALUES(?,?,?,?)",(run,source["id"],timestamp,json.dumps(result)))
                    kind="demo_restock" if result["restocked_units"] else "demo_inventory_check"
                    text=f'{source["name"]}: {result["detail"]}'
                    c.execute("INSERT INTO events(created,kind,agent_id,text) VALUES(?,?,?,?)",(timestamp,kind,source["id"],text))
            with connect() as c: c.execute("UPDATE runs SET finished=?,status='completed' WHERE id=?",(now(),run))
            return {"status":"completed","run_id":run,"checked_agents":len(enabled)}
        except BaseException:
            with connect() as c: c.execute("UPDATE runs SET finished=?,status='failed' WHERE id=?",(now(),run))
            raise

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--reason",choices=["cron","manual","initial"],default="cron")
    print(json.dumps(scan(parser.parse_args().reason)),flush=True)
