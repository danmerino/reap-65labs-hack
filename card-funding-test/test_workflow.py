import concurrent.futures
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import workflow_worker as worker
import agent_flow as flow
import inventory


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.patches=[patch.object(flow,'DATA',root),patch.object(flow,'DB',root/'flow.sqlite'),
                      patch.object(inventory,'FILE',root/'stock.json')]
        for p in self.patches:p.start()
        inventory.FILE.write_text(json.dumps({'schema_version':1,'mode':'demo','orders':[],'restocks':[],
            'products':[{'id':'matcha-watch','sku':'DEMO','price_sgd_cents':24000,'stock':10,'target_stock':10,
                         'restock_batch':1,'suppliers':{k:{'stock':1,'buy_sgd_cents':100,'price_minor':100}
                                                      for k in ['europe','us','australia']}}]}))
        flow.init();job=flow.claim();flow.finish(job['id'])
        flow.update(stage='ready',quote={'quote_id':'q1','title':'Substitute'})
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_only_live_agent_and_fixed_actions_are_accepted(self):
        for action,agent in [('pay','us'),('fund','australia'),('withdraw','europe')]:
            with self.assertRaises(ValueError):flow.enqueue(action,agent,'demo-1')
    def test_concurrent_pay_clicks_create_one_job(self):
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda _:flow.enqueue('pay','europe','demo-1'),range(4)))
        self.assertEqual(len({r['job_id'] for r in results}),1)
        self.assertEqual(sum(not r['replayed'] for r in results),1)
    def test_cycle_binding_and_payment_in_progress_block_new_funding(self):
        with self.assertRaises(ValueError):flow.enqueue('pay','europe','other-cycle')
        flow.update(stage='settling')
        with self.assertRaises(ValueError):flow.enqueue('fund','europe','demo-1')
    def test_completion_without_settlement_never_adds_stock(self):
        flow.enqueue('pay','europe','demo-1')
        flow.record_payment({'payment_id':'p1','quote_id':'q1','state':'completed','settled':False})
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],10)
        self.assertEqual(flow.state()['stage'],'reconcile')
    def test_settlement_adds_one_demo_unit_and_replays_do_not_add_more(self):
        flow.enqueue('pay','europe','demo-1')
        value={'payment_id':'p1','quote_id':'q1','state':'completed','settled':True,'charged_test_usdc':'1.000000'}
        flow.record_payment(value);flow.record_payment(value)
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],11)
        self.assertEqual(len(inventory.snapshot()['sandbox_stock_receipts']),1)
        flow.record_payment(value|{'state':'pending','settled':False})
        self.assertEqual(flow.state()['stage'],'settled')
    def test_wrong_quote_or_charge_never_adds_stock(self):
        flow.enqueue('pay','europe','demo-1')
        value={'payment_id':'p1','quote_id':'other','state':'completed','settled':True,'charged_test_usdc':'1.000000'}
        flow.record_payment(value)
        flow.record_payment(value|{'quote_id':'q1','charged_test_usdc':'2.000000'})
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],10)
    def test_listing_uses_the_actual_fixed_quote_amount(self):
        flow.update(quote={'quote_id':'q1','title':'Fixed sticker stand-in','price_test_usdc':'3.960000'})
        flow.enqueue('pay','europe','demo-1')
        value={'payment_id':'p1','quote_id':'q1','state':'completed','settled':True,'charged_test_usdc':'1.000000'}
        flow.record_payment(value)
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],10)
        flow.record_payment(value|{'charged_test_usdc':'3.960000'})
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],11)
    def test_event_order_is_occurrence_time_not_insertion_order(self):
        flow.event('later','Agent','message','Later',occurred_at='2026-10-09T11:00:02+00:00')
        flow.event('earlier','Chain','transfer','Earlier',occurred_at='2026-10-09T11:00:01+00:00')
        events=flow.public_state()['events'];keys=[e['text'] for e in events]
        self.assertLess(keys.index('Earlier'),keys.index('Later'))
    def settle_cycle(self, payment_id, quote_id):
        flow.enqueue('pay','europe',flow.state()['cycle_id'])
        job=flow.claim();flow.finish(job['id'])
        flow.record_payment({'payment_id':payment_id,'quote_id':quote_id,'state':'completed',
                             'settled':True,'charged_test_usdc':'1.000000'})
    def test_two_successful_purchases_have_distinct_cycles_and_stock_receipts(self):
        self.settle_cycle('p1','q1')
        flow.enqueue('repurchase','europe','demo-1')
        fresh=flow.state()
        self.assertEqual(fresh['cycle_id'],'demo-2')
        self.assertIsNone(fresh['quote'])
        self.assertNotIn('payment',fresh)
        self.assertNotIn('funding_transaction',fresh)
        with flow.db() as c:
            archived=json.loads(c.execute('SELECT document FROM cycles').fetchone()[0])
            self.assertEqual(archived['payment']['payment_id'],'p1')
        job=flow.claim();self.assertEqual(job['action'],'prepare');flow.finish(job['id'])
        flow.update(stage='ready',quote={'quote_id':'q2','title':'Substitute'})
        old={'payment_id':'p1','quote_id':'q1','state':'completed','settled':True,'charged_test_usdc':'1.000000'}
        flow.record_payment(old)
        self.assertEqual(flow.state()['stage'],'ready')
        self.settle_cycle('p2','q2');flow.record_payment(old)
        self.assertEqual(flow.state()['payment']['payment_id'],'p2')
        self.assertEqual(inventory.snapshot()['products'][0]['stock'],12)
        self.assertEqual(len(inventory.snapshot()['sandbox_stock_receipts']),2)
        self.assertEqual(sum(e['kind']=='demo_listing' for e in flow.public_state()['events']),2)
    def test_repurchase_blocked_until_settled_and_worker_finished(self):
        for stage in ('ready','submitted','authorizing','settling','reconcile'):
            flow.update(stage=stage)
            with self.assertRaises(ValueError):flow.enqueue('repurchase','europe','demo-1')
        flow.update(stage='ready');flow.enqueue('pay','europe','demo-1');flow.claim()
        flow.record_payment({'payment_id':'p1','quote_id':'q1','state':'completed','settled':True,'charged_test_usdc':'1.000000'})
        with self.assertRaises(ValueError):flow.enqueue('repurchase','europe','demo-1')
    def test_concurrent_repurchase_clicks_start_only_one_new_cycle(self):
        self.settle_cycle('p1','q1')
        def repeat(_):
            try:return flow.enqueue('repurchase','europe','demo-1')
            except ValueError:return None
        with concurrent.futures.ThreadPoolExecutor(4) as pool:results=list(pool.map(repeat,range(4)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(flow.state()['cycle_id'],'demo-2')
        with self.assertRaises(ValueError):flow.enqueue('pay','europe','demo-1')
    def test_deposit_and_pi_request_ids_are_scoped_to_purchase_cycle(self):
        self.assertEqual(worker.deposit_path().name,'scout-demo-1-deposit.json')
        self.settle_cycle('p1','q1');flow.enqueue('repurchase','europe','demo-1')
        self.assertEqual(worker.deposit_path().name,'scout-demo-2-deposit.json')
        self.assertEqual(worker.cycle_key('pay-intent'),'demo-2:pay-intent')
        self.assertFalse(flow.update(_cycle_id='demo-1',stage='settled'))
        self.assertEqual(flow.state()['stage'],'discovering')
    def test_funding_can_retry_after_refresh_failure_before_transfer(self):
        flow.enqueue('fund','europe','demo-1')
        job=flow.claim();flow.finish(job['id'],'blocked')
        flow.update(stage='reconcile',error='Refresh unavailable')
        retry=flow.enqueue('fund','europe','demo-1')
        self.assertEqual(retry['job_id'],job['id'])
        flow.update(payment={'payment_id':'p1'})
        flow.finish(job['id'],'blocked')
        with self.assertRaises(ValueError):flow.enqueue('fund','europe','demo-1')
    def candidate(self):
        total=SimpleNamespace(currency='USD',decimals=2,minor_units=100)
        return SimpleNamespace(quote_id='q1',payment_id=None,lines=[SimpleNamespace(quantity=1)],total=total,
                               needs_shipping_selection=lambda:False,is_expired=lambda _:False)
    def funding(self,quote,required=1_000_000):
        return SimpleNamespace(required=SimpleNamespace(currency='USDC',decimals=6,minor_units=required),
          quote=SimpleNamespace(token=SimpleNamespace(chain_id=763373,contract_address=worker.app.TEST_USDC),
                                merchant_total=quote.total),state='ready')
    def test_merchant_priced_one_dollar_quote_is_allowed_and_higher_price_blocked(self):
        quote=self.candidate()
        with patch.object(worker.app.kwal,'read_funding',return_value=self.funding(quote)):
            worker.verify_quote(SimpleNamespace(token='not-a-live-token'),quote)
        with patch.object(worker.app.kwal,'read_funding',return_value=self.funding(quote,37_520_000)):
            with self.assertRaises(worker.QuoteBudgetError):worker.verify_quote(SimpleNamespace(token='not-a-live-token'),quote)
        self.assertEqual(flow.state()['observed_quote']['price_test_usdc'],'37.520000')


if __name__=='__main__':unittest.main()
