import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
import chat_store as chat


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.patches=[patch.object(chat,'DATA',root),patch.object(chat,'DB',root/'chat.sqlite')]
        for p in self.patches:p.start()
        chat.init();self.session=str(uuid.uuid4())
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def submit(self,agent='europe',message='Tell me the watch history.',request_id=None):
        return chat.submit(self.session,agent,request_id or str(uuid.uuid4()),message)
    def test_replayed_request_does_not_create_a_second_model_job(self):
        request=str(uuid.uuid4());self.submit(request_id=request)
        self.assertTrue(self.submit(request_id=request)['replayed'])
        self.assertIsNotNone(chat.claim());self.assertIsNone(chat.claim())
    def test_request_id_cannot_change_message_or_agent(self):
        request=str(uuid.uuid4());self.submit(request_id=request)
        with self.assertRaises(ValueError):self.submit('us',request_id=request)
        with self.assertRaises(ValueError):self.submit(message='Changed question',request_id=request)
    def test_conversations_are_isolated_by_agent_and_browser_session(self):
        self.submit();job=chat.claim();chat.complete(job['request_id'],'Europe answer','Pi')
        self.assertEqual(len(chat.read(self.session,'europe')['messages']),2)
        self.assertEqual(chat.read(self.session,'us')['messages'],[])
        self.assertEqual(chat.read(str(uuid.uuid4()),'europe')['messages'],[])
    def test_only_one_outstanding_message_per_thread(self):
        self.submit()
        with self.assertRaises(ValueError):self.submit(message='Second message')
        self.submit('us')
        self.assertTrue(chat.read(self.session,'europe')['busy'])
    def test_invalid_identifiers_and_large_or_empty_messages_are_rejected(self):
        for message in ['', ' '*10,'x'*2001]:
            with self.assertRaises(ValueError):self.submit(message=message)
        with self.assertRaises(ValueError):chat.submit('../credentials','europe',str(uuid.uuid4()),'hello')
        with self.assertRaises(ValueError):self.submit('treasury')
    def test_long_dossier_and_context_keep_demo_delivery_and_preview_boundaries(self):
        dossier=json.loads(chat.DOSSIER.read_text())
        self.assertGreater(len(json.dumps(dossier['products']['matcha-watch']).split()),2000)
        europe=chat.context('europe');us=chat.context('us')
        self.assertEqual(europe['agent']['watch_delivery']['total_business_days'],[6,10])
        self.assertEqual(us['live_context']['inventory_status'],'out_of_stock')
        self.assertFalse(us['live_context']['chat_can_transact'])
        self.assertNotIn('card_status',us['live_context'])
        self.assertNotIn('token',json.dumps(us))
        self.assertEqual(dossier['products']['matcha-watch']['confirmed_product_facts']['glass'],'Resin glass')


if __name__=='__main__':unittest.main()
