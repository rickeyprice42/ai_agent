from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from io import BytesIO
import unittest

from fastapi.testclient import TestClient
from backend.main import app
from backend.service import AgentService
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.types import Message


class AttachmentAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = AgentService.__new__(AgentService)
        self.service.root_dir = root
        self.service.settings = replace(Settings.load(Path(__file__).resolve().parents[1]), model_provider='mock', model_name='mock-local', reasoning_fallback_enabled=False, database_file=root/'db.sqlite', memory_file=root/'memory.json', tool_workspace_dir=root/'workspace')
        self.service.database = AvelinDatabase(self.service.settings.database_file, default_model_provider='mock', default_model_name='mock-local')
        self.service._agents = {}
        self.service._connector_factories = []
        self.a = self.service.database.create_user('a@qa.test','user-a','hash','A')['id']
        self.b = self.service.database.create_user('b@qa.test','user-b','hash','B')['id']

    def tearDown(self): self.temp.cleanup()

    def test_tenant_file_read_write_and_legacy_artifact_isolation(self):
        first = self.service.agent_for_user(self.a)
        second = self.service.agent_for_user(self.b)
        first.files.write_file('private.txt','ONLY-OWNER')
        self.assertNotEqual(first.files.workspace_dir, second.files.workspace_dir)
        with self.assertRaises(ValueError):
            self.service.workspace_file_path(self.b,'private.txt')
        legacy = self.service.settings.tool_workspace_dir/'old.txt'
        legacy.write_text('legacy',encoding='utf8')
        first.memory.append_message(Message(role='assistant',content='created',artifacts=[{'path':'old.txt'}]))
        self.assertEqual(self.service.workspace_file_path(self.a,'old.txt'),legacy)
        with self.assertRaises(ValueError): self.service.workspace_file_path(self.b,'old.txt')
        second.memory.append_message(Message(role='assistant',content='old',artifacts=[{'path':'old.txt'}]))
        with self.assertRaises(ValueError): self.service.workspace_file_path(self.a,'old.txt')

    def test_attachment_text_context_and_foreign_path_are_checked(self):
        file = self.service.upload_attachment(self.a,'данные.csv',b'name,value\nA,42')
        context = self.service.message_with_attachments(self.a,'sum',[file['path']])
        self.assertIn('A,42',context)
        with self.assertRaises(ValueError): self.service.message_with_attachments(self.b,'sum',[file['path']])
        with self.assertRaises(ValueError): self.service.message_with_attachments(self.a,'sum',['../../private.txt'])

    def test_upload_rejects_execution_traversal_invalid_documents_and_image_mismatch(self):
        for name,body in [('x.exe',b'exe'),('../x.txt',b'test'),('x.docx',b'badzip'),('x.png',b'bad-image'),('x.pdf',b'notpdf'),('private:stream.txt',b'ads'),('CON.txt',b'reserved')]:
            with self.subTest(name=name),self.assertRaises(ValueError): self.service.upload_attachment(self.a,name,body)

    def test_raw_upload_http_auth_and_size_limit(self):
        with patch('backend.routes.agent_service',self.service):
            client=TestClient(app)
            result=client.post('/api/auth/register',json={'email':'api@qa.test','username':'api-user','password':'password123','display_name':'API'})
            headers={'Authorization':'Bearer '+result.json()['token'],'X-Filename':'test.txt'}
            self.assertEqual(client.post('/api/attachments',content=b'hello').status_code,401)
            good=client.post('/api/attachments',headers=headers,content=b'hello')
            self.assertEqual(good.status_code,200,good.text)
            self.assertTrue(good.json()['path'].startswith('attachments/'))
            bad=client.post('/api/attachments',headers=headers,content=b'x'*(10*1024*1024+1))
            self.assertEqual(bad.status_code,413)

    def test_malformed_pdf_chat_returns_safe_error_instead_of_traceback(self):
        file=self.service.upload_attachment(self.a,'broken.pdf',b'%PDF-1.7\nnot-a-valid-document')
        with self.assertRaises(ValueError):self.service.message_with_attachments(self.a,'read',[file['path']])

    def test_overlapping_streams_are_rejected_and_cancel_releases_chat(self):
        from backend.service import ChatBusyError
        thread,events=self.service.chat_stream('hello',self.a)
        next(events)
        with self.assertRaises(ChatBusyError):self.service.chat('overlap',self.a,thread)
        events.close()
        result=self.service.chat('after cancel',self.a,thread)
        self.assertEqual(result['thread_id'],thread)
        _,unstarted=self.service.chat_stream('unstarted',self.a,thread)
        unstarted.close()
        self.assertTrue(self.service.chat('released',self.a,thread)['reply'])

    def test_auth_duplicates_wrong_password_logout_and_parallel_sessions(self):
        with patch('backend.routes.agent_service',self.service):
            client=TestClient(app)
            body={'email':'auth@qa.test','username':'auth-user','password':'strong-password-123','display_name':'QA'}
            registered=client.post('/api/auth/register',json=body)
            self.assertEqual(registered.status_code,200)
            first={'Authorization':'Bearer '+registered.json()['token']}
            self.assertEqual(client.post('/api/auth/register',json=body).status_code,409)
            self.assertEqual(client.post('/api/auth/login',json={'login':'auth-user','password':'incorrect-password'}).status_code,401)
            self.assertEqual(client.get('/api/auth/me',headers={'Authorization':'Bearer invalid'}).status_code,401)
            login=client.post('/api/auth/login',json={'login':'auth-user','password':body['password']})
            second={'Authorization':'Bearer '+login.json()['token']}
            self.assertEqual(client.get('/api/auth/me',headers=second).status_code,200)
            self.assertEqual(client.post('/api/auth/logout',headers=first).status_code,200)
            self.assertEqual(client.get('/api/auth/me',headers=first).status_code,401)
            self.assertEqual(client.get('/api/auth/me',headers=second).status_code,200)

    def test_sqlite_backup_restores_messages_and_integrity(self):
        import sqlite3
        from contextlib import closing
        self.service.chat('persistent audit marker',self.a)
        target=Path(self.temp.name)/'restored.sqlite3'
        with closing(sqlite3.connect(self.service.settings.database_file)) as source,closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
            self.assertEqual(destination.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertGreater(destination.execute("SELECT count(*) FROM messages WHERE content LIKE '%persistent audit marker%'").fetchone()[0],0)

if __name__=='__main__': unittest.main()
