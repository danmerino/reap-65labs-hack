"""Fictional stock and demo orders, atomically persisted in one JSON file."""
import copy
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

FILE=Path(os.environ.get("SCOUT_INVENTORY_FILE",str(Path(__file__).resolve().parent/"demo-stock.json")))

class InventoryError(Exception):
    pass

def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def validate(data):
    if not isinstance(data,dict) or data.get("mode")!="demo" or data.get("schema_version")!=1 or not isinstance(data.get("products"),list):
        raise InventoryError("Demo inventory is unavailable")
    ids=set()
    for p in data["products"]:
        if not isinstance(p,dict) or not isinstance(p.get("id"),str) or p["id"] in ids:
            raise InventoryError("Invalid demo product")
        ids.add(p["id"])
        for key in ("price_sgd_cents","stock","target_stock","restock_batch"):
            if type(p.get(key)) is not int or p[key]<0: raise InventoryError("Invalid inventory quantity or price")
        if p["price_sgd_cents"]==0 or not isinstance(p.get("suppliers"),dict): raise InventoryError("Invalid inventory price or suppliers")
        for s in p["suppliers"].values():
            if not isinstance(s,dict): raise InventoryError("Invalid demo supplier")
            for key in ("stock","buy_sgd_cents","price_minor"):
                if type(s.get(key)) is not int or s[key]<0: raise InventoryError("Invalid demo supplier quantity or price")
    if not isinstance(data.get("orders"),list) or not isinstance(data.get("restocks"),list):
        raise InventoryError("Invalid demo ledger")
    watch=next((p for p in data["products"] if p["id"]=="matcha-watch"),None)
    if watch is None or not all(a in watch["suppliers"] for a in ("europe","us","australia")):
        raise InventoryError("Configured demo watch or supplier is missing")

@contextmanager
def transaction():
    with FILE.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            original=FILE.read_bytes();data=json.loads(original);validate(data)
        except (OSError,ValueError) as e: raise InventoryError("Demo inventory is unavailable") from e
        yield data
        validate(data)
        encoded=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode()
        if encoded!=original:
            fd,name=tempfile.mkstemp(prefix=".scout-stock-",dir=FILE.parent)
            try:
                with os.fdopen(fd,"wb") as out: out.write(encoded);out.flush();os.fsync(out.fileno())
                os.replace(name,FILE)
            finally:
                if os.path.exists(name): os.unlink(name)

def snapshot():
    with transaction() as data: return copy.deepcopy(data)

def purchase(product_id,quantity,order_id):
    if not isinstance(product_id,str) or type(quantity) is not int or not 1<=quantity<=10: raise ValueError("Invalid product or quantity")
    with transaction() as data:
        prior=next((o for o in data["orders"] if o["id"]==order_id),None)
        if prior:
            if prior["product_id"]!=product_id or prior["quantity"]!=quantity: raise ValueError("This order ID belongs to another basket")
            return {"order":copy.deepcopy(prior),"created":False}
        product=next((p for p in data["products"] if p["id"]==product_id),None)
        if not product: raise ValueError("Invalid product or quantity")
        if product["stock"]<quantity: raise InventoryError("Not enough stock")
        product["stock"]-=quantity
        order={"id":order_id,"product_id":product_id,"quantity":quantity,"unit_price_sgd_cents":product["price_sgd_cents"],
               "total_sgd_cents":quantity*product["price_sgd_cents"],"created":timestamp(),"status":"demo_order","payment":"not_charged"}
        data["orders"].append(order)
        return {"order":copy.deepcopy(order),"created":True}

def manage_stock(enabled,run_id):
    results={}
    with transaction() as data:
        for product in data["products"]:
            for agent_id in enabled:
                supplier=product["suppliers"].get(agent_id)
                if not supplier: continue
                units=min(max(0,product["target_stock"]-product["stock"]),product["restock_batch"],supplier["stock"])
                if units:
                    product["stock"]+=units;supplier["stock"]-=units
                    data["restocks"].append({"run_id":run_id,"agent_id":agent_id,"product_id":product["id"],"quantity":units,
                                             "buy_sgd_cents":supplier["buy_sgd_cents"],"created":timestamp(),"mode":"demo"})
                result=results.setdefault(agent_id,{"supplier_stock":0,"store_stock":0,"restocked_units":0,"products":[],"evidence":"demo_json","mode":"demo"})
                result["supplier_stock"]+=supplier["stock"];result["store_stock"]+=product["stock"];result["restocked_units"]+=units
                result["products"].append({"product_id":product["id"],"sku":product["sku"],"supplier_stock":supplier["stock"],"store_stock":product["stock"],"restocked_units":units})
        for result in results.values():
            result["stock"]="in_stock" if result["supplier_stock"] else "out_of_stock"
            result["detail"]=f"Demo JSON: checked {len(result['products'])} products; added {result['restocked_units']} units to the shop."
    return results
