from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import base64
import json
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from ai_agent.database import AvelinDatabase
from ai_agent.web_push import WebPushService, PushConfig, validate_subscription
from backend.main import app
from backend.routes import current_user
from backend.service import AgentService
from ai_agent.config import Settings
from scripts.configure_web_push import configure


def b64(data):return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class WebPushTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=AvelinDatabase(self.root/'db.sqlite')
        self.db.ensure_user_defaults('alice');self.db.ensure_user_defaults('bob')
        public=configure(self.root/'keys')
        self.config=PushConfig(self.root/'keys/private.pem',public)
        self.calls=[]
        self.service=WebPushService(self.db,self.config,sender=lambda sub,payload:self.calls.append((sub,payload)) or 201)
        key=ec.generate_private_key(ec.SECP256R1())
        self.subscription={'endpoint':'https://fcm.googleapis.com/fcm/send/test-device', 'keys':{
            'p256dh':b64(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)),
            'auth':b64(b'0123456789abcdef')}}

    def tearDown(self):self.temp.cleanup()

    def notify(self, user='alice', suffix='one', kind='task_report'):
        return self.db.create_in_app_notification(user,deduplication_key='run-report:'+suffix+':completed',
            kind=kind,title='Private task title',body='SECRET-PROMPT-NEVER-ON-LOCKSCREEN',entity_type='task_report',entity_id='report')

    def test_opt_in_excludes_old_notifications_then_persists_delivery_and_never_repeats(self):
        self.notify(suffix='before')
        self.service.subscribe('alice',self.subscription)
        self.notify();self.notify(user='bob')
        self.assertEqual(self.service.deliver_pending()[0]['state'],'delivered')
        restarted=WebPushService(self.db,self.config,sender=lambda *_:self.fail('must not resend'))
        self.assertEqual(restarted.deliver_pending(),[])
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('SECRET',json.dumps(self.calls[0][1]))
        self.assertNotIn('Private task',json.dumps(self.calls[0][1]))
        self.assertEqual(self.calls[0][1]['data']['url'],'/?view=automations')

    def test_same_subscription_re_registration_preserves_pending_notifications(self):
        first=self.service.subscribe('alice',self.subscription)
        self.notify()
        second=self.service.subscribe('alice',self.subscription)
        self.assertEqual(first,second)
        self.assertEqual(len(self.service.deliver_pending()),1)

    def test_cross_user_cannot_hijack_or_unsubscribe_another_users_device(self):
        self.service.subscribe('alice',self.subscription)
        with self.assertRaisesRegex(ValueError,'другому аккаунту'):
            self.service.subscribe('bob',self.subscription)
        self.service.unsubscribe('bob',self.subscription['endpoint'])
        self.assertEqual(self.service.public_config('alice')['subscription_count'],1)
        self.assertEqual(self.service.public_config('bob')['subscription_count'],0)

    def test_unsubscribe_cancels_queued_delivery_and_removes_keys(self):
        self.service.subscribe('alice',self.subscription)
        self.notify();self.service.enqueue_notifications()
        self.service.unsubscribe('alice',self.subscription['endpoint'])
        self.assertEqual(self.service.deliver_pending(),[])
        with self.db.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0],0)

    def test_endpoint_and_key_validation_rejects_ssrf_credentials_and_bad_curve(self):
        for endpoint in ('http://fcm.googleapis.com/x','https://127.0.0.1/x','https://user:secret@web.push.apple.com/x',
                'https://web.push.apple.com.evil.test/x','https://fcm.googleapis.com:444/x','https://fcm.googleapis.com/x#fragment'):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):
                validate_subscription({**self.subscription,'endpoint':endpoint})
        with self.assertRaises(ValueError):validate_subscription({**self.subscription,'keys':{'p256dh':b64(b'\x04'+b'x'*64),'auth':b64(b'x'*16)}})
        with self.assertRaises(ValueError):validate_subscription({**self.subscription,'keys':{'p256dh':self.subscription['keys']['p256dh'],'auth':'bad'}})

    def test_transient_failure_is_durable_bounded_retry_without_raw_error(self):
        self.service.subscribe('alice',self.subscription);self.notify()
        self.service.sender=lambda *_:503
        self.assertEqual(self.service.deliver_pending()[0]['state'],'pending')
        self.assertEqual(self.service.deliver_pending(),[])
        with self.db.connect() as db:db.execute("UPDATE push_deliveries SET next_attempt_at='2000-01-01'")
        def broken(*_):raise RuntimeError('SECRET-ENDPOINT-TOKEN')
        self.service.sender=broken
        self.service.deliver_pending()
        with self.db.connect() as db:
            row=db.execute('SELECT * FROM push_deliveries').fetchone()
            self.assertEqual(row['last_error'],'push_transport_error')
            db.execute("UPDATE push_deliveries SET next_attempt_at='2000-01-01',attempts=5")
        self.assertEqual(self.service.deliver_pending()[0]['state'],'failed')

    def test_expired_device_is_disabled_and_not_retried(self):
        self.service.subscribe('alice',self.subscription);self.notify()
        self.service.sender=lambda *_:410
        self.assertEqual(self.service.deliver_pending()[0]['state'],'failed')
        self.assertEqual(self.service.public_config('alice')['subscription_count'],0)
        self.notify(suffix='next')
        self.assertEqual(self.service.deliver_pending(),[])

    def test_multiple_workers_claim_once_and_crash_lease_is_recoverable(self):
        self.service.subscribe('alice',self.subscription);self.notify()
        def sender(sub,payload):time.sleep(.03);self.calls.append((sub,payload));return 201
        one=WebPushService(self.db,self.config,sender);two=WebPushService(self.db,self.config,sender)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda s:s.deliver_pending(),(one,two)))
        self.assertEqual(len(self.calls),1)
        self.notify(suffix='lease');self.service.enqueue_notifications()
        with self.db.connect() as db:db.execute("UPDATE push_deliveries SET state='sending',locked_until='2000-01-01' WHERE state='pending'")
        self.assertEqual(len(self.service.deliver_pending()),1)

    def test_real_library_encrypts_payload_signs_vapid_and_disables_redirects(self):
        self.service.subscribe('alice',self.subscription);self.notify()
        real=WebPushService(self.db,self.config)
        with patch('requests.Session.post',return_value=SimpleNamespace(status_code=201,text='',headers={})) as post:
            self.assertEqual(real.deliver_pending()[0]['state'],'delivered')
        options=post.call_args.kwargs
        self.assertFalse(options['allow_redirects'])
        self.assertEqual(options['timeout'],8)
        self.assertIsInstance(options['data'],bytes)
        self.assertNotIn('Результат'.encode('utf-8'),options['data'])
        self.assertEqual(options['headers']['content-encoding'],'aes128gcm')
        self.assertTrue(options['headers']['authorization'].startswith('vapid '))

    def test_stale_worker_cannot_overwrite_a_newer_delivery_claim(self):
        self.service.subscribe('alice',self.subscription);self.notify()
        def stale_sender(*_):
            with self.db.connect() as db:
                db.execute("UPDATE push_deliveries SET state='delivered',claim_token='replacement-claim'")
            return 503
        self.service.sender=stale_sender
        self.assertEqual(self.service.deliver_pending()[0]['state'],'superseded')
        with self.db.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM push_deliveries').fetchone()[0],'delivered')

    def test_config_has_no_secrets_and_mismatched_keys_disable_push(self):
        config=self.service.public_config('alice')
        self.assertTrue(config['enabled'])
        self.assertNotIn('private',json.dumps(config))
        bad=WebPushService(self.db,replace(self.config,public_key='wrong'))
        self.assertFalse(bad.public_config('alice')['enabled'])

    def test_authenticated_endpoints_queue_test_and_scope_unsubscribe(self):
        service=AgentService.__new__(AgentService)
        service.root_dir=self.root;service.database=self.db;service._web_push_service=self.service
        original=dict(app.dependency_overrides)
        app.dependency_overrides[current_user]=lambda:{'id':'alice'}
        try:
            with patch('backend.routes.agent_service',service):
                client=TestClient(app)
                self.assertTrue(client.get('/api/notifications/push/config').json()['enabled'])
                self.assertEqual(client.put('/api/notifications/push/subscription',json=self.subscription).status_code,200)
                test=client.post('/api/notifications/push/test')
                self.assertEqual(test.status_code,200,test.text)
                self.assertEqual(test.json(),{'ok':True,'queued':1})
                self.assertEqual(len(self.service.deliver_pending()),1)
                self.assertEqual(client.request('DELETE','/api/notifications/push/subscription',json={'endpoint':self.subscription['endpoint']}).status_code,200)
        finally:app.dependency_overrides=original
        with patch('backend.routes.agent_service',service):
            self.assertEqual(TestClient(app).get('/api/notifications/push/config').status_code,401)


if __name__=='__main__':unittest.main()
