import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import patch

temporary=tempfile.TemporaryDirectory(prefix="scout-demo-test-")
os.environ["SCOUT_DATA_DIR"]=temporary.name
os.environ["SCOUT_INVENTORY_FILE"]=str(Path(temporary.name)/"demo-stock.json")
import inventory,scanner,store,server
import card_monitor

class CardMonitorTests(unittest.TestCase):
    def setUp(self):
        self.file=Path(temporary.name)/'card-status.json'
    def tearDown(self):
        self.file.unlink(missing_ok=True)
    def test_public_projection_strips_credentials_and_sensitive_links(self):
        self.file.write_text(json.dumps({'schema_version':1,'mode':'kwal_sandbox','checked_at':store.now(),
            'token':'private-jwt','card':{'status':'ACTIVE','pan':'private-pan','spendable_test_usdc':'1.000000'},
            'payment':{'state':'not_submitted','approval_url':'private-link','settled':False}}))
        result=card_monitor.read_status(self.file)
        text=json.dumps(result)
        self.assertNotIn('private',text);self.assertFalse(result['stale'])
        self.assertEqual(result['card']['spendable_test_usdc'],'1.000000')
        self.assertEqual(result['payment']['state'],'not_submitted')
    def test_stale_or_failed_observation_never_looks_fresh(self):
        for stamp,error in [('2026-01-01T00:00:00+00:00',False),(store.now(),True)]:
            self.file.write_text(json.dumps({'schema_version':1,'mode':'kwal_sandbox','checked_at':stamp,'refresh_error':error}))
            self.assertTrue(card_monitor.read_status(self.file)['stale'])
    def test_missing_or_malformed_snapshot_is_unavailable(self):
        self.file.unlink(missing_ok=True)
        self.assertFalse(card_monitor.read_status(self.file)['available'])
        self.file.write_text('{bad json')
        self.assertFalse(card_monitor.read_status(self.file)['available'])

def reset(stock=8):
    inventory.FILE.write_text(json.dumps({"schema_version":1,"mode":"demo","products":[{
        "id":"matcha-watch","sku":"AQ-230ECK-3A","price_sgd_cents":24000,"stock":stock,"target_stock":10,"restock_batch":2,
        "suppliers":{k:{"stock":n,"buy_sgd_cents":p,"price_minor":p,"currency":"SGD"} for k,n,p in [('europe',12,14500),('us',6,16500),('australia',4,21500)]}
    }],"orders":[],"restocks":[]}))

class InventoryTests(unittest.TestCase):
    def setUp(self):reset()
    def test_fixed_price_and_stock_decrement_are_atomic(self):
        r=inventory.purchase('matcha-watch',2,str(uuid.uuid4()))
        self.assertEqual(r['order']['total_sgd_cents'],48000)
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],6)
        self.assertEqual(r['order']['payment'],'not_charged')
    def test_idempotent_retry_does_not_consume_stock_twice(self):
        oid=str(uuid.uuid4());inventory.purchase('matcha-watch',2,oid)
        r=inventory.purchase('matcha-watch',2,oid)
        self.assertFalse(r['created']);self.assertEqual(inventory.snapshot()['products'][0]['stock'],6)
    def test_idempotent_key_cannot_change_basket(self):
        oid=str(uuid.uuid4());inventory.purchase('matcha-watch',2,oid)
        with self.assertRaises(ValueError):inventory.purchase('matcha-watch',3,oid)
    def test_concurrent_last_unit_has_one_winner(self):
        reset(1)
        def buy():
            try:inventory.purchase('matcha-watch',1,str(uuid.uuid4()));return 'ok'
            except inventory.InventoryError:return 'sold_out'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r=list(pool.map(lambda _:buy(),range(2)))
        self.assertEqual(sorted(r),['ok','sold_out']);self.assertEqual(inventory.snapshot()['products'][0]['stock'],0)
    def test_invalid_quantities_rejected(self):
        for n in [0,-1,True,1.5,11]:
            with self.assertRaises(ValueError):inventory.purchase('matcha-watch',n,str(uuid.uuid4()))
    def test_restock_respects_target_and_supplier_stock(self):
        r=inventory.manage_stock(['europe','us','australia'],1)
        self.assertEqual(r['europe']['restocked_units'],2)
        self.assertEqual(r['us']['restocked_units'],0)
        data=inventory.snapshot()['products'][0]
        self.assertEqual(data['stock'],10);self.assertEqual(data['suppliers']['europe']['stock'],10)
    def test_paused_agent_is_not_used(self):
        r=inventory.manage_stock(['us','australia'],1)
        self.assertNotIn('europe',r);self.assertEqual(r['us']['restocked_units'],2)
        self.assertEqual(inventory.snapshot()['products'][0]['suppliers']['europe']['stock'],12)
    def test_empty_supplier_never_creates_stock(self):
        with inventory.transaction() as d:
            d['products'][0]['stock']=0
            for s in d['products'][0]['suppliers'].values():s['stock']=0
        inventory.manage_stock(['europe','us','australia'],1)
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],0)
    def test_missing_inventory_fails_closed(self):
        inventory.FILE.unlink()
        with self.assertRaises(inventory.InventoryError):inventory.snapshot()
    def test_agent_cycle_does_not_fetch_real_suppliers(self):
        store.init()
        with patch('urllib.request.urlopen',side_effect=AssertionError('Unexpected supplier request')):
            r=scanner.scan('initial')
        self.assertEqual(r['status'],'completed')
        self.assertTrue(all(json.loads(o['result'])['mode']=='demo' for o in store.latest().values()))

class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset();store.init();cls.servers=[]
        for role in ['public','dashboard']:
            s=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);s.role=role
            threading.Thread(target=s.serve_forever,daemon=True).start();cls.servers.append(s)
        cls.public='http://127.0.0.1:'+str(cls.servers[0].server_port);cls.private='http://127.0.0.1:'+str(cls.servers[1].server_port)
    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:s.shutdown();s.server_close()
    def setUp(self):reset()
    def request(self,origin,path,body=None,headers=None):
        req=urllib.request.Request(origin+path,data=json.dumps(body).encode() if body is not None else None,
            headers=headers or ({'Content-Type':'application/json','X-Scout-Request':'1'} if body is not None else {}))
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def test_public_buy_uses_server_price(self):
        body={'product_id':'matcha-watch','quantity':1,'order_id':str(uuid.uuid4()),'price_sgd_cents':1}
        status,d=self.request(self.public,'/api/orders',body);self.assertEqual(status,201)
        self.assertEqual(d['order']['total_sgd_cents'],24000)
    def test_stock_and_orders_are_shared_with_dashboard(self):
        self.request(self.public,'/api/orders',{'product_id':'matcha-watch','quantity':1,'order_id':str(uuid.uuid4())})
        _,d=self.request(self.private,'/api/dashboard');self.assertEqual(d['inventory']['stock'],7);self.assertEqual(d['inventory']['orders_count'],1)
    def test_sold_out_checkout_rejected(self):
        reset(0);status,_=self.request(self.public,'/api/orders',{'product_id':'matcha-watch','quantity':1,'order_id':str(uuid.uuid4())})
        self.assertEqual(status,409)
    def test_public_catalog_shows_fixed_price_and_stock(self):
        _,d=self.request(self.public,'/api/catalog');self.assertEqual(d['price_sgd_cents'],24000);self.assertEqual(d['stock'],8);self.assertTrue(d['demo_mode'])
        self.assertNotIn('suppliers',d);self.assertNotIn('reported_buy_sgd_cents',d['product'])
    def test_public_raw_files_and_unlisted_controls_are_unavailable(self):
        for path in ['/api/dashboard','/demo-stock.json','/data/scout.sqlite3']:
            self.assertEqual(self.request(self.public,path)[0],404)
        self.assertEqual(self.request(self.public,'/api/scan',{})[0],404)
    def test_public_demo_dashboard_is_available(self):
        status,d=self.request(self.public,'/dashboard/api/dashboard')
        self.assertEqual(status,200);self.assertEqual(d['mode'],'demo_json');self.assertNotIn('interest',d)
    def test_payment_controls_reject_preview_agents_and_caller_amounts(self):
        status,_=self.request(self.public,'/dashboard/api/agent-demo',{'action':'fund','agent_id':'us','cycle_id':'demo-1'})
        self.assertEqual(status,409)
        status,_=self.request(self.public,'/dashboard/api/agent-demo',{'action':'pay','agent_id':'europe','cycle_id':'demo-1','amount':100})
        self.assertEqual(status,400)
        self.assertEqual(self.request(self.public,'/api/agent-demo')[0],404)
    def test_chat_is_discussion_only_and_isolated_from_financial_controls(self):
        session=str(uuid.uuid4());request=str(uuid.uuid4())
        body={'session_id':session,'agent_id':'us','request_id':request,'message':'Tell me about the watch.'}
        self.assertEqual(self.request(self.public,'/dashboard/api/chat',body)[0],202)
        status,d=self.request(self.public,'/dashboard/api/chat?session_id='+session+'&agent_id=us')
        self.assertEqual(status,200);self.assertTrue(d['busy']);self.assertEqual(len(d['messages']),1)
        self.assertEqual(self.request(self.public,'/dashboard/api/chat',body|{'action':'fund'})[0],400)
        self.assertEqual(self.request(self.public,'/api/chat?session_id='+session+'&agent_id=us')[0],404)
    def test_public_demo_dashboard_controls_are_available(self):
        status,_=self.request(self.public,'/dashboard/api/agent',{'id':'europe','enabled':False});self.assertEqual(status,200)
        _,d=self.request(self.public,'/dashboard/api/dashboard');self.assertFalse(d['agents'][0]['enabled'])
        self.request(self.public,'/dashboard/api/agent',{'id':'europe','enabled':True})
    def test_cross_origin_control_is_rejected(self):
        status,_=self.request(self.private,'/api/agent',{'id':'europe','enabled':False},{'X-Scout-Request':'1','Origin':'https://unrelated.example'})
        self.assertEqual(status,403)
    def test_legacy_bid_form_endpoint_removed(self):
        self.assertEqual(self.request(self.public,'/api/interest',{})[0],404)

if __name__=='__main__':unittest.main()
