import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from catalog import PRODUCT, PRODUCTS, SOURCES
from inventory import snapshot
from card_monitor import read_status

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SCOUT_DATA_DIR", str(ROOT / "data")))
DB = DATA / "scout.sqlite3"

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        with connection:
            yield connection
    finally:
        connection.close()

def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT NOT NULL, finished TEXT, reason TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, agent_id TEXT NOT NULL, checked TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS interests(visitor_hash TEXT PRIMARY KEY, product_id TEXT NOT NULL, target_cents INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, agent_id TEXT, text TEXT NOT NULL);
        """)
        for s in SOURCES:
            c.execute("INSERT OR IGNORE INTO agents(id) VALUES(?)", (s["id"],))
        if not c.execute("SELECT 1 FROM events LIMIT 1").fetchone():
            c.execute("INSERT INTO events(created,kind,text) VALUES(?,?,?)", (now(), "watch_added", "Matcha watch added. Three scouts check supplier stock every five minutes."))

def latest():
    with connect() as c:
        return {s["id"]: dict(row) if (row := c.execute(
            "SELECT * FROM observations WHERE agent_id=? ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()) else None for s in SOURCES}

def demand():
    with connect() as c:
        row = c.execute("SELECT COUNT(*) count,MIN(target_cents) low,MAX(target_cents) high FROM interests WHERE product_id=?", (PRODUCT["id"],)).fetchone()
        return dict(row)

def interest(visitor_hash, target):
    timestamp = now()
    with connect() as c:
        c.execute("INSERT INTO interests VALUES(?,?,?,?,?) ON CONFLICT(visitor_hash) DO UPDATE SET target_cents=excluded.target_cents,updated=excluded.updated", (visitor_hash,PRODUCT["id"],target,timestamp,timestamp))
    return demand()

def public_catalog():
    data=snapshot();products=[]
    for p in data["products"]:
        metadata=PRODUCTS.get(p["id"],{"id":p["id"],"name":p.get("name",p["id"]),"subtitle":"Demo item","image":"assets/pi-board.svg","description":"Simulated sourcing and inventory."})
        products.append({**{k:metadata.get(k,"") for k in ("id","name","subtitle","image","description")},"sku":p["sku"],
            "price_sgd_cents":p["price_sgd_cents"],"stock":p["stock"],"available_to_buy":p["stock"]>0,"demo_mode":True,
            "status":"in_stock" if p["stock"] else "sold_out"})
    first=next(p for p in products if p["id"]==PRODUCT["id"])
    return {"products":products,"product":first,"price_sgd_cents":first["price_sgd_cents"],"stock":first["stock"],
            "available_to_buy":first["available_to_buy"],"demo_mode":True,"message":f"{first['stock']} in stock" if first["stock"] else "Sold out"}

def dashboard():
    inventory=snapshot();item=next(p for p in inventory["products"] if p["id"]==PRODUCT["id"])
    observations = latest()
    agents = []
    with connect() as c:
        enabled = {r["id"]:bool(r["enabled"]) for r in c.execute("SELECT * FROM agents")}
        runs = [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10")]
        events = [dict(r) for r in c.execute("SELECT * FROM events WHERE kind LIKE 'demo_%' ORDER BY id DESC LIMIT 20")]
    for source in SOURCES:
        row = observations[source["id"]]
        agents.append({**source, "enabled":enabled[source["id"]], "checked":row["checked"] if row else None,
                       "observation":json.loads(row["result"]) if row else None,
                       "card_status":"demo_simulated", "purchase_enabled":False})
    buy=item["suppliers"]["europe"]["buy_sgd_cents"];low=high=item["price_sgd_cents"]
    stamp = datetime.now(timezone.utc)
    next_epoch = ((int(stamp.timestamp()) // 300) + 1) * 300
    product_rows=[]
    for p in inventory["products"]:
        meta=PRODUCTS.get(p["id"],{"name":p["id"],"image":"assets/pi-board.svg"})
        costs=[s["buy_sgd_cents"] for s in p["suppliers"].values() if s["stock"]>0]
        cost=min(costs) if costs else min(s["buy_sgd_cents"] for s in p["suppliers"].values())
        product_rows.append({**p,"name":meta["name"],"image":meta["image"],"best_buy_sgd_cents":cost,
                             "gross_margin_pct":round((p["price_sgd_cents"]-cost)*100/p["price_sgd_cents"],1)})
    supplier_totals={a["id"]:sum(p["suppliers"].get(a["id"],{}).get("stock",0) for p in inventory["products"]) for a in SOURCES}
    return {"product":PRODUCT,"products":product_rows,"agents":agents,"runs":runs,"events":events,
            "card_demo":read_status(DATA / "card-status.json"),
            "mode":"demo_json","inventory":{"file":"scout/demo-stock.json","stock":item["stock"],"target_stock":item["target_stock"],"price_sgd_cents":item["price_sgd_cents"],
                "total_stock":sum(p["stock"] for p in inventory["products"]),"supplier_totals":supplier_totals,
                "suppliers":item["suppliers"],"orders_count":len(inventory["orders"]),"units_sold":sum(o["quantity"] for o in inventory["orders"]),
                "orders":inventory["orders"][-10:][::-1],"restocks":inventory["restocks"][-10:][::-1]},
            "schedule":{"expression":"*/5 * * * *","interval_seconds":300,"next_scan":datetime.fromtimestamp(next_epoch,timezone.utc).isoformat(),"timezone":"UTC"},
            "economics":{"spread_low_cents":low-buy,"spread_high_cents":high-buy,
                         "gross_margin_low_pct":round((low-buy)*100/low,1),"gross_margin_high_pct":round((high-buy)*100/high,1),
                         "markup_low_pct":round((low-buy)*100/buy,1) if buy else 0,"net_margin":None,"costs":"Demo economics, before shipping and fees."},
            "payments":{"mode":"demo","note":"All stock, restocking and customer purchases are simulated. No money is charged."},
            "notifications":{"mode":"dashboard_feed","mobile_push_connected":False},
            "server_time":now()}
