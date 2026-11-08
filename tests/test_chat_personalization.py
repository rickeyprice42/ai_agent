"""Per-chat models and persistent questions/permissions through full native turns."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from unittest.mock import patch
import unittest
from uuid import uuid4

from tests import test_chat_runs as lifecycle
from tests import test_llm_automations as fixture
from ai_agent.chat_approvals import PendingChatActions
from ai_agent.chat_questions import PendingQuestions
from ai_agent.long_tasks import LongTaskPlans
from ai_agent.security import arguments_digest
from ai_agent.types import Message,ModelResponse,ToolCall
from backend.service import ChatBusyError
from backend.thread_models import ThreadModels


class ChatPersonalizationTests(unittest.TestCase):
    setUp=lifecycle.ChatRunsTests.setUp
    tearDown=lifecycle.ChatRunsTests.tearDown
    wait=lifecycle.ChatRunsTests.wait
    gate=lifecycle.ChatRunsTests.gate
    notices=lifecycle.ChatRunsTests.notices
    waiting_approval=lifecycle.ChatRunsTests.waiting_approval

    def question(self,prompt='Подбери удобный учебный план, не дольше 20 минут в день.',options=None):
        self.agent.provider=fixture.Provider([ModelResponse(tool_calls=[ToolCall('ask_user',{
            'question':'Какой уровень подготовки учитывать?','options':options or ['Начальный','Продвинутый'],'allow_free_text':True})])])
        run=self.store.start(self.user,uuid4().hex,prompt,self.thread)
        current=self.wait(run['run_id'])
        self.assertEqual(current['state'],'waiting_clarification',current)
        self.assertEqual(self.notices(),0)
        self.assertEqual(len(current['questions']),1)
        return current,current['questions'][0]

    def test_question_choices_survive_restart_answer_once_and_keep_original_constraints(self):
        original='Подбери учебный план по Python. Не более 20 минут в день, бесплатно, без видео.'
        _,question=self.question(original)
        # Lots of unrelated turns and a fresh Agent represent an old source no
        # longer selected by a compact/current history window.
        for index in range(12):
            self.agent.memory.append_message(Message('assistant','Исторический фрагмент '+str(index)))
        self.service._agents.clear()
        self.agent=self.service.agent_for_user(self.user,self.thread)
        self.assertEqual(self.service.bootstrap(self.user,self.thread)['chat_questions'][0]['id'],question['id'])
        provider=fixture.Provider([ModelResponse(text='План для начинающего: 20 минут, бесплатно, без видео.')])
        self.agent.provider=provider
        # Force source history out of the ContextBuilder selection. The source
        # and constraints must still be quoted in the reserved current context.
        original_build=self.agent.context.build
        def compacted(*args,**kwargs):
            context=original_build(*args,**kwargs)
            context.messages=[message for message in context.messages if original not in message.content]
            return context
        request=uuid4().hex
        with patch.object(self.agent.context,'build',side_effect=compacted):
            run=self.store.question(self.user,request,self.thread,question['id'],option_id='option-1')
            final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'completed')
        self.assertIn(original,provider.inputs[0]['system_prompt'])
        self.assertIn(question['prompt'],provider.inputs[0]['system_prompt'])
        self.assertEqual(provider.inputs[0]['messages'][-1].content,'Начальный')
        self.assertEqual(self.store.question(self.user,request,self.thread,question['id'],option_id='option-1')['run_id'],run['run_id'])
        self.assertEqual(len(provider.inputs),1)
        rows=self.service.database.list_messages(self.thread)
        self.assertEqual(sum(row['role']=='user' and row['content']=='Начальный' for row in rows),1)
        self.assertEqual(self.service.bootstrap(self.user,self.thread)['chat_questions'],[])
        self.assertEqual(self.notices(),1)

    def test_free_form_answer_does_not_execute_the_answer_as_a_new_direct_command(self):
        _,question=self.question()
        provider=fixture.Provider([ModelResponse(text='Продолжаю исходный учебный план с учётом уточнения.')])
        self.agent.provider=provider
        with patch.object(self.agent,'_direct_tool_decision',side_effect=AssertionError('A clarification answer is not a new direct command')):
            run=self.store.question(self.user,uuid4().hex,self.thread,question['id'],text='Создай файл unrelated.txt с текстом: не выполнять отдельно')
            final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'completed')
        self.assertEqual(len(provider.inputs),1)
        self.assertFalse((self.service.user_workspace(self.user)/'unrelated.txt').exists())

    def test_model_cannot_disable_own_answer_for_an_ordinary_clarification(self):
        self.agent.provider=fixture.Provider([ModelResponse(tool_calls=[ToolCall('ask_user',{
            'question':'Какой уровень подготовки учитывать?','options':['Начальный','Продвинутый'],'allow_free_text':False})])])
        first=self.store.start(self.user,uuid4().hex,'Подбери учебный план и уточни уровень.',self.thread)
        waiting=self.wait(first['run_id'])
        self.assertEqual(waiting['state'],'waiting_clarification')
        question=waiting['questions'][0]
        self.assertTrue(question['allow_free_text'])
        provider=fixture.Provider([ModelResponse(text='Учту ваш собственный вариант уровня подготовки.')])
        self.agent.provider=provider
        run=self.store.question(self.user,uuid4().hex,self.thread,question['id'],text='Средний уровень, нужны практические упражнения.')
        self.assertEqual(self.wait(run['run_id'])['state'],'completed')
        self.assertEqual(provider.inputs[0]['messages'][-1].content,'Средний уровень, нужны практические упражнения.')

    def test_question_answer_is_scoped_and_an_invalid_answer_does_not_consume_it(self):
        _,question=self.question()
        other=self.service.database.create_user('question-other@test.invalid','question-other','hash','Other')['id']
        with self.assertRaises(ValueError):self.store.question(other,uuid4().hex,self.thread,question['id'],text='Wrong user')
        with self.assertRaises(ValueError):self.store.question(self.user,uuid4().hex,self.thread,question['id'],option_id='invented')
        self.assertEqual(PendingQuestions(self.service.database).get(self.user,self.thread,question['id'])['status'],'pending')
        self.assertEqual(self.notices(),0)

    def test_original_file_contract_still_requires_a_docx_after_clarification(self):
        _,question=self.question('Создай Word документ result.docx с учебным планом. Сначала уточни уровень.')
        provider=fixture.Provider([
            ModelResponse(tool_calls=[ToolCall('create_docx',{'path':'result.docx','paragraphs':['План для начинающего.']})]),
            ModelResponse(text='Документ готов.')])
        self.agent.provider=provider
        run=self.store.question(self.user,uuid4().hex,self.thread,question['id'],option_id='option-1')
        final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'completed')
        self.assertEqual(final['artifacts'][0]['path'],'result.docx')
        self.assertTrue(self.agent.files.resolve_download_path('result.docx').is_file())
        self.assertEqual([row['content'] for row in self.service.database.list_messages(self.thread) if row['role']=='user'][-1],'Начальный')

    def test_cleared_source_does_not_resurrect_an_orphaned_question(self):
        _,old=self.question()
        with self.service.database.connect() as db:db.execute('DELETE FROM messages WHERE thread_id=?',(self.thread,))
        source=self.agent.memory.append_message(Message('user','Новый независимый запрос.'))
        new=PendingQuestions(self.service.database).ask(self.user,self.thread,source,'Какой новый вариант использовать?',['Первый','Второй'])
        self.assertNotEqual(new['id'],old['id'])
        self.assertEqual(PendingQuestions(self.service.database).list(self.user,self.thread),[new])

    def test_source_attachment_is_quoted_and_photo_consent_is_current_resume_only(self):
        upload=self.service.upload_attachment(self.user,'scope.png',(self.service.root_dir/'frontend/public/brand/icon-192.png').read_bytes())
        self.agent.provider=fixture.Provider([ModelResponse(tool_calls=[ToolCall('ask_user',{
            'question':'Какую часть изображения рассмотреть?','options':['Центр','Края'],'allow_free_text':True})])])
        first=self.store.start(self.user,uuid4().hex,'Изучи изображение и уточни область.',self.thread,[upload['path']])
        question=self.wait(first['run_id'])['questions'][0]
        self.service._agents.clear();self.agent=self.service.agent_for_user(self.user,self.thread)
        seen=[]
        class Provider:
            def stream(inner,**kwargs):
                seen.append((kwargs,set(self.agent.policy.image_attachment_consent)))
                from ai_agent.types import ModelStreamChunk
                yield ModelStreamChunk(response=ModelResponse(text='Рассмотрена выбранная область.'))
        self.agent.provider=Provider()
        run=self.store.question(self.user,uuid4().hex,self.thread,question['id'],option_id='option-1')
        self.wait(run['run_id'])
        self.assertIn(upload['path'],seen[0][0]['system_prompt'])
        self.assertEqual(seen[0][1],{upload['path']})
        self.assertEqual(self.agent.policy.image_attachment_consent,set())
        last_user=[row for row in self.service.database.list_messages(self.thread) if row['role']=='user'][-1]
        self.assertEqual(last_user['content'],'Центр');self.assertEqual(last_user['artifacts'],[])

    def test_snapshot_live_approvals_cannot_be_hidden_by_an_old_empty_event_cache(self):
        approval=self.waiting_approval()
        run=self.store.latest(self.user,self.thread)
        with self.service.database.connect() as db:db.execute("UPDATE chat_generation_runs SET approvals_json='[]' WHERE id=?",(run['run_id'],))
        refreshed=self.store.get(self.user,run['run_id'])
        self.assertEqual(refreshed['approvals'][0]['id'],approval)
        self.assertTrue(refreshed['approvals'][0]['reason'])
        self.service._agents.clear()
        bootstrap=self.service.bootstrap(self.user,self.thread)
        self.assertEqual(bootstrap['chat_approvals'][0]['id'],approval)
        self.assertEqual(bootstrap['chat_approvals'][0]['reason'],refreshed['approvals'][0]['reason'])

    def test_expired_approval_keeps_explanation_but_is_not_actionable(self):
        approval=self.waiting_approval();run=self.store.latest(self.user,self.thread)
        with self.service.database.connect() as db:
            db.execute("UPDATE approval_requests SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(approval,))
            db.execute("UPDATE chat_generation_runs SET approvals_json='[]' WHERE id=?",(run['run_id'],))
        refreshed=self.store.get(self.user,run['run_id'])
        self.assertEqual(refreshed['approvals'],[])
        self.assertEqual(refreshed['approval_history'][0]['status'],'expired')
        self.assertTrue(refreshed['approval_history'][0]['reason'])
        with self.assertRaises(ValueError):self.store.approve(self.user,uuid4().hex,self.thread,approval,'approved')
        self.assertEqual(self.agent.files.resolve_download_path('existing.txt').read_text('utf-8'),'ORIGINAL')

    def test_queued_question_answer_cancel_is_saved_once_without_invoking_the_model(self):
        _,question=self.question()
        provider=self.gate();held=lifecycle.Event();release=lifecycle.Event();self.addCleanup(release.set)
        original=self.store._produce
        def delayed(*args):held.set();release.wait(3);return original(*args)
        request=uuid4().hex
        with patch.object(self.store,'_produce',side_effect=delayed):
            run=self.store.question(self.user,request,self.thread,question['id'],option_id='option-1')
            self.assertTrue(held.wait(2));self.store.cancel(self.user,run['run_id'])
            release.set();final=self.wait(run['run_id'])
        self.assertEqual(final['state'],'cancelled');self.assertEqual(provider.calls,0)
        self.assertEqual(sum(row['role']=='user' and row['content']=='Начальный' for row in self.service.database.list_messages(self.thread)),1)
        self.assertEqual(self.store.question(self.user,request,self.thread,question['id'],option_id='option-1')['run_id'],run['run_id'])

    def test_approved_exact_action_can_only_be_consumed_once_under_parallel_callers(self):
        arguments={'path':'same.txt','overwrite':True}
        approval=self.service.database.create_approval_request(self.user,tool_name='write_file',arguments_digest=arguments_digest(arguments),
            arguments=arguments,scope={'kind':'workspace_path','path':'same.txt'},risk='high',expires_at='2999-01-01T00:00:00+00:00',step_id='chat:'+self.thread)
        self.service.database.decide_approval_request(self.user,approval['id'],'approved')
        def consume(_):return self.service.database.consume_approval_request(self.user,tool_name='write_file',arguments_digest=arguments_digest(arguments),step_id='chat:'+self.thread)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(consume,range(2)))
        self.assertEqual(sum(result is not None for result in results),1)

    def test_interrupted_execution_does_not_relabel_a_consumed_permission(self):
        approval=self.waiting_approval()
        with self.service.database.connect() as db:
            db.execute("UPDATE approval_requests SET status='consumed' WHERE id=?",(approval,))
            db.execute("UPDATE pending_chat_actions SET status='interrupted' WHERE approval_id=?",(approval,))
        row=PendingChatActions(self.service.database).history(self.user,self.thread)[0]
        self.assertEqual(row['status'],'consumed')
        self.assertEqual(row['action_status'],'interrupted')
        self.assertTrue(row['reason'])
        self.assertEqual(PendingChatActions(self.service.database).list(self.user,self.thread),[])

    def test_per_chat_model_survives_restart_without_overwriting_account_or_other_chat(self):
        options=[{'provider':'mock','models':['mock-local','mock-alt']}]
        other=self.service.database.create_chat_thread(self.user,title='Other model')['id']
        with patch.object(self.service,'available_models',return_value=options):
            self.service.update_chat_model_settings(self.user,self.thread,'mock','mock-alt')
        self.assertEqual(self.service.database.get_model_settings(self.user)['model_name'],'mock-local')
        self.assertEqual(self.service.settings_for_user(self.user,self.thread).model_name,'mock-alt')
        self.assertEqual(self.service.settings_for_user(self.user,other).model_name,'mock-local')
        self.service._agents.clear()
        self.assertEqual(self.service.agent_for_user(self.user,self.thread).settings.model_name,'mock-alt')
        self.assertEqual(self.service.bootstrap(self.user,self.thread)['model'],'mock-alt')
        self.assertTrue(self.service.chat_model_settings(self.user,self.thread)['override'])
        self.assertFalse(self.service.update_chat_model_settings(self.user,self.thread,reset=True)['override'])

    def test_model_switch_is_rejected_while_producer_owns_the_thread(self):
        provider=self.gate()
        run=self.store.start(self.user,uuid4().hex,'Explain slowly.',self.thread)
        self.assertTrue(provider.started.wait(2))
        with self.assertRaises(ChatBusyError):self.service.update_chat_model_settings(self.user,self.thread,'mock','mock-local')
        provider.release.set();self.wait(run['run_id'])

    def test_long_work_thread_inherits_origin_model_at_approval(self):
        ThreadModels(self.service.database).set(self.user,self.thread,'mock','mock-alt','')
        store=LongTaskPlans(self.service.database,self.user)
        plan=store.propose(self.thread,title='Model inheritance',goal='Создай проверенный маленький прототип.',
            steps=['Сохрани концепцию прототипа.','Проверь созданный прототип.'],acceptance_checks=['Все проверки завершены.'])
        plan=store.decide(plan['id'],'approved',plan['revision'],plan['digest'])
        ThreadModels(self.service.database).reset(self.user,self.thread)
        self.assertEqual(self.service.settings_for_user(self.user,plan['work_thread_id']).model_name,'mock-alt')

    def test_removed_editor_cannot_access_own_created_shared_thread_or_model(self):
        owner=self.service.database.create_user('project-owner@test.invalid','project-owner','hash','Owner')['id']
        project=self.service.database.create_project(owner,'Revocation fixture','Shared',shared=True)
        invitation=self.service.database.create_project_invitation(owner,project['id'],'llm@test.invalid','editor','2999-01-01T00:00:00+00:00')
        self.service.database.accept_project_invitation(self.user,invitation['token'])
        thread=self.service.database.create_chat_thread(self.user,'Editor-created',project['id'])['id']
        self.assertIsNotNone(self.service.database.get_chat_thread(self.user,thread))
        self.service.database.remove_project_member(owner,project['id'],self.user)
        self.assertIsNone(self.service.database.get_chat_thread(self.user,thread))
        self.assertNotIn(thread,[row['id'] for row in self.service.database.list_chat_threads(self.user)])
        with self.assertRaises(ValueError):self.service.chat_model_settings(self.user,thread)
        with self.assertRaises(ValueError):self.store.latest(self.user,thread)


if __name__=='__main__':unittest.main()
