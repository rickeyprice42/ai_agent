"""Detached generation lifecycle, ownership and completion/cancel regressions."""
from threading import Event
from time import monotonic,sleep
from unittest.mock import patch
import unittest
from uuid import uuid4

from tests import test_llm_automations as fixture
from ai_agent.llm.base import ProviderError
from ai_agent.types import ModelResponse,ModelStreamChunk,ToolCall
from backend.chat_runs import ACTIVE,chat_runs_for
from backend.service import ChatBusyError


class GatedProvider:
    def __init__(self):
        self.started=Event();self.release=Event();self.calls=0
    def stream(self,**_):
        self.calls+=1
        yield ModelStreamChunk(delta='Partial')
        self.started.set()
        if not self.release.wait(5):raise ProviderError('Fixture timeout','timeout')
        yield ModelStreamChunk(delta=' final')
        yield ModelStreamChunk(response=ModelResponse(text='Partial final'))


class ChatRunsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.LLMAutomationTests();self.fixture.setUp()
        self.service=self.fixture.service;self.user=self.fixture.user
        self.thread=self.service.database.create_chat_thread(self.user,title='Detached chat fixture')['id']
        self.agent=self.service.agent_for_user(self.user,self.thread)
        self.gates=[];self.stores=[]
        self.store=chat_runs_for(self.service);self.stores.append(self.store)

    def tearDown(self):
        for gate in self.gates:gate.release.set()
        for store in self.stores:
            deadline=monotonic()+3
            while store._workers and monotonic()<deadline:sleep(.01)
            store.close()
        self.fixture.tearDown()

    def gate(self):
        provider=GatedProvider();self.gates.append(provider);self.agent.provider=provider
        return provider

    def wait(self,run_id,state=None):
        deadline=monotonic()+5
        while monotonic()<deadline:
            item=self.store.get(self.user,run_id)
            if (state and item['state']==state) or (not state and item['state'] not in ACTIVE):return item
            sleep(.01)
        self.fail('Generation did not reach expected state: '+str(item))

    def notices(self):
        with self.service.database.connect() as db:
            return db.execute("SELECT COUNT(*) FROM in_app_notifications WHERE user_id=? AND kind='chat_completed'",(self.user,)).fetchone()[0]

    def test_request_retry_is_idempotent_and_thread_stays_owned_without_subscribers(self):
        provider=self.gate();request=uuid4().hex
        started=self.store.start(self.user,request,'Give a careful answer.',self.thread)
        self.assertTrue(provider.started.wait(2))
        again=self.store.start(self.user,request,'Give a careful answer.',self.thread)
        self.assertEqual(again['run_id'],started['run_id'])
        with self.assertRaises(ChatBusyError):self.store.start(self.user,request,'Different prompt.',self.thread)
        with self.assertRaises(ChatBusyError):self.store.start(self.user,uuid4().hex,'Concurrent prompt.',self.thread)
        with self.assertRaises(ChatBusyError):self.service.chat_stream('Legacy overlap',self.user,self.thread)
        provider.release.set()
        result=self.wait(started['run_id'])
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['text'],'Partial final')
        self.assertTrue(result['message_id']);self.assertTrue(result['user_message_id'])
        self.assertEqual(result['user_text'],'Give a careful answer.')
        self.assertEqual(provider.calls,1);self.assertEqual(self.notices(),1)
        self.assertEqual(self.store.by_request(self.user,request)['run_id'],result['run_id'])
        self.assertEqual(self.store.latest(self.user,self.thread)['run_id'],result['run_id'])
        self.assertEqual(self.store.start(self.user,request,'Give a careful answer.',self.thread)['state'],'completed')
        self.assertEqual(provider.calls,1)

    def test_other_user_cannot_read_replay_cancel_or_lookup_request(self):
        provider=self.gate();request=uuid4().hex
        item=self.store.start(self.user,request,'Private prompt.',self.thread)
        other=self.service.database.create_user('other-run@test.invalid','other-run','hash','Other')['id']
        for operation in (lambda:self.store.get(other,item['run_id']),lambda:self.store.cancel(other,item['run_id']),
                lambda:self.store.events_after(other,item['run_id']),lambda:self.store.latest(other,self.thread)):
            with self.assertRaises(ValueError):operation()
        self.assertIsNone(self.store.by_request(other,request))
        self.assertFalse(self.store.get(self.user,item['run_id'])['cancel_requested'])
        provider.release.set();self.wait(item['run_id'])

    def test_provider_error_after_partial_is_failed_and_not_user_cancellation(self):
        class Broken:
            def stream(self,**_):
                yield ModelStreamChunk(delta='Saved partial')
                raise ProviderError('Сервис временно недоступен.','upstream_unavailable')
        self.agent.provider=Broken()
        run=self.store.start(self.user,uuid4().hex,'Explain this carefully.',self.thread)
        final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'failed');self.assertEqual(final['outcome'],'failed')
        self.assertIn('Saved partial',final['text']);self.assertEqual(self.notices(),0)
        _,events=self.store.events_after(self.user,run['run_id'])
        self.assertEqual(events[-1]['state'],'failed')
        self.assertEqual(sum(event['type']=='done' for event in events),1)

    def test_accepted_cancel_wins_before_completion_notification_claim(self):
        before_claim=Event();release_claim=Event();self.addCleanup(release_claim.set)
        original=self.service._notify_chat_completion
        def waiting(*args):
            before_claim.set();release_claim.wait(3);return original(*args)
        self.agent.provider=fixture.Provider([ModelResponse(text='A finished response.')])
        with patch.object(self.service,'_notify_chat_completion',side_effect=waiting):
            run=self.store.start(self.user,uuid4().hex,'Answer briefly.',self.thread)
            self.assertTrue(before_claim.wait(2))
            self.assertTrue(self.store.cancel(self.user,run['run_id'])['cancel_requested'])
            release_claim.set()
            final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'cancelled')
        self.assertEqual(self.notices(),0)

    def test_committed_completion_wins_a_later_cancel_without_falsely_cancelling(self):
        claimed=Event();release_notice=Event();self.addCleanup(release_notice.set)
        original=self.service.database.create_in_app_notification
        def waiting(*args,**kwargs):
            if kwargs.get('kind')=='chat_completed':claimed.set();release_notice.wait(3)
            return original(*args,**kwargs)
        self.agent.provider=fixture.Provider([ModelResponse(text='Known complete response.')])
        with patch.object(self.service.database,'create_in_app_notification',side_effect=waiting):
            run=self.store.start(self.user,uuid4().hex,'Answer briefly.',self.thread)
            self.assertTrue(claimed.wait(2))
            self.assertFalse(self.store.cancel(self.user,run['run_id'])['cancel_requested'])
            release_notice.set();final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'completed');self.assertEqual(self.notices(),1)

    def test_shutdown_restores_interrupted_partial_without_replaying_provider(self):
        provider=self.gate()
        run=self.store.start(self.user,uuid4().hex,'A long answer please.',self.thread)
        self.assertTrue(provider.started.wait(2))
        partial=self.store.get(self.user,run['run_id'])
        self.assertEqual(partial['text'],'Partial')
        self.store.close()
        self.service._chat_run_service=None
        self.store=chat_runs_for(self.service);self.stores.append(self.store)
        restored=self.store.get(self.user,run['run_id'])
        self.assertEqual(restored['state'],'interrupted')
        self.assertEqual(restored['text'],'Partial')
        provider.release.set();sleep(.1)
        self.assertEqual(provider.calls,1);self.assertEqual(self.notices(),0)
        row=next(message for message in self.service.database.list_messages(self.thread) if message['id']==partial['message_id'])
        self.assertEqual(row['status'],'failed')
        self.assertNotIn('пользователем',row['error'])
        ui=next(message for message in self.service.bootstrap(self.user,self.thread)['history'] if message.get('content')==row['content'])
        self.assertEqual(ui['status'],'failed')
        self.assertEqual(ui['id'],partial['message_id'])

    def waiting_approval(self):
        self.agent.files.write_file('existing.txt','ORIGINAL')
        self.agent.provider=fixture.Provider([ModelResponse(tool_calls=[ToolCall('write_file',{
            'path':'existing.txt','content':'APPROVED CHANGE','overwrite':True})])])
        run=self.store.start(self.user,uuid4().hex,'Please update my existing file.',self.thread)
        waiting=self.wait(run['run_id'])
        self.assertEqual(waiting['state'],'waiting_approval')
        self.assertEqual(len(waiting['approvals']),1)
        return waiting['approvals'][0]['id']

    def test_approval_continuation_is_detached_and_action_happens_once(self):
        approval=self.waiting_approval();provider=self.gate();request=uuid4().hex
        resumed=self.store.approve(self.user,request,self.thread,approval,'approved')
        self.assertTrue(provider.started.wait(2))
        self.assertEqual(self.store.approve(self.user,request,self.thread,approval,'approved')['run_id'],resumed['run_id'])
        provider.release.set();final=self.wait(resumed['run_id'])
        self.assertEqual(final['state'],'completed');self.assertEqual(self.notices(),1)
        self.assertEqual(self.agent.files.resolve_download_path('existing.txt').read_text('utf-8'),'APPROVED CHANGE')
        with self.service.database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM action_logs WHERE user_id=? AND tool_name='write_file' AND status='completed'",(self.user,)).fetchone()[0],1)

    def test_approval_denial_never_inherits_previous_completed_outcome_or_notice(self):
        approval=self.waiting_approval()
        self.agent.last_turn_outcome='completed'  # Previous state must never label denial a success.
        run=self.store.approve(self.user,uuid4().hex,self.thread,approval,'denied')
        final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'cancelled');self.assertEqual(final['outcome'],'cancelled')
        self.assertEqual(self.notices(),0)
        self.assertEqual(self.agent.files.resolve_download_path('existing.txt').read_text('utf-8'),'ORIGINAL')

    def test_cancel_before_producer_starts_does_not_call_the_provider(self):
        held=Event();release=Event();self.addCleanup(release.set)
        provider=self.gate()
        original=self.store._produce
        def delayed(*args):
            held.set();release.wait(3);return original(*args)
        with patch.object(self.store,'_produce',side_effect=delayed):
            run=self.store.start(self.user,uuid4().hex,'Queued response.',self.thread)
            self.assertTrue(held.wait(2))
            self.assertEqual(run['state'],'queued')
            self.assertTrue(self.store.cancel(self.user,run['run_id'])['cancel_requested'])
            release.set();final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'cancelled')
        self.assertEqual(provider.calls,0);self.assertEqual(self.notices(),0)
        messages=[row for row in self.service.database.list_messages(self.thread) if row['role']=='user']
        self.assertEqual([row['content'] for row in messages],['Queued response.'])
        self.assertEqual(final['user_message_id'],messages[0]['id'])
        self.assertEqual(self.store.start(self.user,run['request_id'],'Queued response.',self.thread)['run_id'],run['run_id'])
        self.assertEqual(len([row for row in self.service.database.list_messages(self.thread) if row['role']=='user']),1)

    def test_queued_cancellation_preserves_uploaded_attachment_and_clean_display_prompt(self):
        held=Event();release=Event();self.addCleanup(release.set)
        provider=self.gate()
        upload=self.service.upload_attachment(self.user,'notes.txt',b'Extracted attachment context')
        original=self.store._produce
        def delayed(*args):held.set();release.wait(3);return original(*args)
        with patch.object(self.store,'_produce',side_effect=delayed):
            run=self.store.start(self.user,uuid4().hex,'Review my notes.',self.thread,[upload['path']])
            self.assertTrue(held.wait(2))
            self.store.cancel(self.user,run['run_id'])
            release.set();final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'cancelled');self.assertEqual(provider.calls,0)
        messages=[row for row in self.service.database.list_messages(self.thread) if row['role']=='user']
        self.assertEqual(len(messages),1)
        self.assertIn('Extracted attachment context',messages[0]['content'])
        displayed=[row for row in self.service.bootstrap(self.user,self.thread)['history'] if row['role']=='user']
        self.assertEqual(displayed[0]['content'],'Review my notes.')
        self.assertEqual(displayed[0]['artifacts'][0]['path'],upload['path'])
        self.assertEqual(final['user_message_id'],messages[0]['id'])

    def test_failed_attachment_validation_does_not_add_a_user_message(self):
        provider=self.gate()
        with self.assertRaises(ValueError):
            self.store.start(self.user,uuid4().hex,'Invalid upload.',self.thread,['attachments/missing/file.txt'])
        self.assertEqual(self.service.database.list_messages(self.thread),[])
        self.assertEqual(provider.calls,0)

    def test_replaced_manager_cannot_notify_from_an_old_interrupted_producer(self):
        started=Event();release=Event();self.addCleanup(release.set)
        class FinalAfterGate:
            def stream(self,**_):
                yield ModelStreamChunk(delta='Partial')
                started.set();release.wait(3)
                # No intermediate delta: completion would notify before the old
                # consumer regains control unless ownership is checked centrally.
                yield ModelStreamChunk(response=ModelResponse(text='Partial'))
        self.agent.provider=FinalAfterGate()
        run=self.store.start(self.user,uuid4().hex,'A gated answer.',self.thread)
        self.assertTrue(started.wait(2))
        self.store.close()
        self.store=chat_runs_for(self.service);self.stores.append(self.store)
        release.set();sleep(.15)
        final=self.store.get(self.user,run['run_id'])
        self.assertEqual(final['state'],'interrupted')
        self.assertEqual(self.notices(),0)
        history=self.service.database.list_messages(self.thread)
        self.assertEqual(next(row for row in history if row['id']==final['message_id'])['status'],'failed')


if __name__=='__main__':unittest.main()
