from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))
from ai_agent.chat_titles import ChatTitleService, plan_legacy_titles, normalize_generated_title
from ai_agent.database import AvelinDatabase
from ai_agent.memory import MemoryStore
from ai_agent.types import Message


class ChatTitleRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite'
        self.memory=MemoryStore(self.path,user_id='alice');self.db=self.memory.database

    def tearDown(self):self.temp.cleanup()

    def legacy(self,prompt='Привет',topic='Посоветуй монитор для работы'):
        # Simulate history created by the historical server, without new title hooks.
        self.db.add_message('user',prompt,thread_id=self.memory.thread_id)
        if topic:self.db.add_message('user',topic,thread_id=self.memory.thread_id)
        normalized=' '.join(prompt.split());title=normalized[:48].rstrip('.,!?;:')
        if len(normalized)>len(title):title+='...'
        with self.db.connect() as conn:conn.execute('UPDATE chat_threads SET title=? WHERE id=?',(title,self.memory.thread_id))
        return title

    def plan(self):
        with self.db.connect() as conn:return plan_legacy_titles(conn,['alice'])

    def test_greeting_defers_job_until_a_real_topic_arrives(self):
        self.memory.append_message(Message('user','Привет 👋'))
        self.assertIsNone(ChatTitleService(self.db).get_job('alice',self.memory.thread_id))
        source=self.memory.append_message(Message('user','Помоги выбрать монитор для работы'))
        job=ChatTitleService(self.db).get_job('alice',self.memory.thread_id)
        self.assertEqual(job['source_message_id'],source)
        self.assertIn('монитора',self.db.get_chat_thread('alice',self.memory.thread_id)['title'])

    def test_generated_greeting_is_rejected_as_a_topic_title(self):
        for title in ('Привет','Привет 👋','Hello','Hi Avelin'):
            self.assertIsNone(normalize_generated_title(title))
        self.assertEqual(normalize_generated_title('Возможности Avelin'),'Возможности Avelin')

    def test_plan_and_apply_repair_legacy_greeting_preserving_every_message(self):
        self.legacy()
        before=self.db.list_messages(self.memory.thread_id)
        plan=self.plan();self.assertEqual(len(plan),1)
        self.assertEqual(plan[0]['reason'],'exact_legacy_auto_formula')
        result=ChatTitleService(self.db).apply_legacy_plan(plan)
        self.assertEqual(result['applied'],[self.memory.thread_id])
        self.assertEqual(self.db.list_messages(self.memory.thread_id),before)
        self.assertIn('монитора',self.db.get_chat_thread('alice',self.memory.thread_id)['title'])
        self.assertEqual(ChatTitleService(self.db).get_job('alice',self.memory.thread_id)['source_message_id'],before[1]['id'])

    def test_manual_and_nonmatching_historical_titles_are_excluded(self):
        self.legacy()
        self.db.update_chat_thread('alice',self.memory.thread_id,title='Привет')
        self.assertEqual(self.plan(),[])
        other=self.db.create_chat_thread('alice',title='Моё личное название')
        self.db.add_message('user','Привет',thread_id=other['id'])
        self.assertEqual(self.plan(),[])

    def test_manual_rename_after_plan_cannot_be_overwritten(self):
        self.legacy();plan=self.plan()
        self.db.update_chat_thread('alice',self.memory.thread_id,title='Chosen manually')
        result=ChatTitleService(self.db).apply_legacy_plan(plan)
        self.assertEqual(result['applied'],[])
        self.assertEqual(self.db.get_chat_thread('alice',self.memory.thread_id)['title'],'Chosen manually')

    def test_changed_source_after_plan_is_skipped(self):
        self.legacy();plan=self.plan()
        source=self.db.list_messages(self.memory.thread_id)[1]
        self.db.update_message(source['id'],content='Теперь другой вопрос',status='complete')
        self.assertEqual(ChatTitleService(self.db).apply_legacy_plan(plan)['applied'],[])

    def test_greeting_only_history_has_placeholder_without_a_fake_model_job(self):
        self.legacy(topic=None);plan=self.plan()
        ChatTitleService(self.db).apply_legacy_plan(plan)
        self.assertEqual(self.db.get_chat_thread('alice',self.memory.thread_id)['title'],'Новый диалог')
        self.assertIsNone(ChatTitleService(self.db).get_job('alice',self.memory.thread_id))


if __name__=='__main__':unittest.main()
