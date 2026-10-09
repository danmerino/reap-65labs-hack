"""Demo shop and dashboard; public dashboard exposes only this demo's routes."""
import argparse
import json
import mimetypes
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from catalog import PRODUCT, SOURCES
from store import ROOT, connect, init, now, public_catalog, dashboard
from inventory import purchase, InventoryError
import agent_flow
import chat_store

class Handler(BaseHTTPRequestHandler):
    def route(self):
        path=urlsplit(self.path).path
        if self.server.role=="public" and path.startswith("/dashboard/"):
            return "dashboard",path[len("/dashboard"):]
        return self.server.role,path
    def reply(self,status,body,kind="application/json"):
        payload=json.dumps(body,ensure_ascii=False).encode() if kind=="application/json" else body
        self.send_response(status); self.send_header("Content-Type",kind+("; charset=utf-8" if kind.startswith("text/") or kind=="application/json" else ""))
        self.send_header("Content-Length",str(len(payload))); self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Referrer-Policy","same-origin")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers(); self.wfile.write(payload)
    def do_GET(self):
        if urlsplit(self.path).path=="/dashboard" and self.server.role=="public":
            self.send_response(302);self.send_header("Location","/dashboard/");self.send_header("Content-Length","0");self.end_headers();return
        role,path=self.route()
        if path=="/api/chat" and role=="dashboard":
            query=parse_qs(urlsplit(self.path).query)
            try:
                if set(query)!={"session_id","agent_id"} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid chat request')
                return self.reply(200,chat_store.read(query['session_id'][0],query['agent_id'][0]))
            except ValueError as e:return self.reply(400,{"error":str(e)})
        if path=="/api/agent-demo" and role=="dashboard":
            return self.reply(200,agent_flow.public_state())
        if path=="/api/catalog":
            try: return self.reply(200,public_catalog())
            except InventoryError: return self.reply(503,{"error":"Inventory temporarily unavailable"})
        if path=="/api/dashboard" and role=="dashboard":
            try: return self.reply(200,dashboard())
            except InventoryError: return self.reply(503,{"error":"Demo inventory temporarily unavailable"})
        files={"/":"index.html","/index.html":"index.html","/app.js":"app.js","/agent.js":"agent.js","/chat.js":"chat.js","/style.css":"style.css",**{f"/assets/{n}":f"assets/{n}" for n in ("matcha-watch.png","icon.svg","pi-board.svg","pi-zero.svg","keyboard.svg")}}
        if path in files:
            base=ROOT/"web"/("public" if path.startswith("/assets/") else role)
            p=base/files[path]
            if p.is_file(): return self.reply(200,p.read_bytes(),mimetypes.guess_type(str(p))[0] or "application/octet-stream")
        return self.reply(404,{"error":"Not found"})
    def do_POST(self):
        role,path=self.route()
        if self.headers.get("X-Scout-Request")!="1": return self.reply(403,{"error":"Unsupported request"})
        origin=self.headers.get("Origin")
        if origin and urlsplit(origin).netloc!=self.headers.get("Host"): return self.reply(403,{"error":"Origin mismatch"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=(12000 if path=="/api/chat" else 4096): raise ValueError()
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict): raise ValueError()
        except (ValueError,json.JSONDecodeError): return self.reply(400,{"error":"Invalid request"})
        if role=="dashboard" and path=="/api/chat":
            if set(body)!={"session_id","agent_id","request_id","message"}:
                return self.reply(400,{"error":"Only an item chat message is accepted"})
            try:return self.reply(202,chat_store.submit(body['session_id'],body['agent_id'],body['request_id'],body['message']))
            except ValueError as e:return self.reply(409,{"error":str(e)})
        if role=="dashboard" and path=="/api/agent-demo":
            if set(body)!={"action","agent_id","cycle_id"}:
                return self.reply(400,{"error":"Only a fixed demo action is accepted"})
            try: return self.reply(202,agent_flow.enqueue(body["action"],body["agent_id"],body["cycle_id"]))
            except ValueError as e: return self.reply(409,{"error":str(e)})
        if path=="/api/orders":
            order_id=body.get("order_id","")
            if not isinstance(order_id,str) or not re.fullmatch(r"[a-f0-9-]{36}",order_id): return self.reply(400,{"error":"Invalid order ID"})
            try: result=purchase(body.get("product_id"),body.get("quantity"),order_id)
            except ValueError as e: return self.reply(400,{"error":str(e)})
            except InventoryError as e: return self.reply(409 if str(e)=="Not enough stock" else 503,{"error":str(e)})
            if result["created"]:
                try:
                    with connect() as c: c.execute("INSERT INTO events(created,kind,text) VALUES(?,?,?)",(now(),"demo_order",f"Demo order: {result['order']['quantity']} × {result['order']['product_id']} bought at the fixed website price."))
                except Exception: pass # JSON order/stock transaction is authoritative.
            return self.reply(201 if result["created"] else 200,{**result,"demo_mode":True})
        if role=="dashboard" and path=="/api/scan":
            subprocess.Popen([sys.executable,str(ROOT/"scanner.py"),"--reason","manual"],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return self.reply(202,{"accepted":True})
        if role=="dashboard" and path=="/api/agent":
            if body.get("id") not in {s["id"] for s in SOURCES} or type(body.get("enabled")) is not bool:
                return self.reply(400,{"error":"Invalid agent"})
            with connect() as c: c.execute("UPDATE agents SET enabled=? WHERE id=?",(int(body["enabled"]),body["id"]))
            return self.reply(200,{"saved":True})
        return self.reply(404,{"error":"Not found"})

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--role",choices=["public","dashboard"],required=True);p.add_argument("--port",type=int,required=True)
    args=p.parse_args();init();agent_flow.init();chat_store.init();server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler);server.role=args.role
    print(f"Scout {args.role} listening on 127.0.0.1:{args.port}",flush=True);server.serve_forever()
