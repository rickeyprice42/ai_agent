from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import os
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.tools.base import Tool, ToolRegistry, ToolPermission, RetryPolicy
from ai_agent.tools.process_host import HostedHandler, ProcessHandlerSpec


class HostContractTests(unittest.TestCase):
    def test_descriptor_requires_importable_name_and_json_configuration(self):
        with self.assertRaises(TypeError):ProcessHandlerSpec('host_process_fixture:run',ROOT,configuration={'bad':object()})
        with self.assertRaises(ValueError):ProcessHandlerSpec('module:__dict__',ROOT)

    def test_unknown_mutation_stops_chat_batch_and_further_model_calls(self):
        from tests.test_runtime_audit import isolated_agent
        from ai_agent.types import ModelResponse,ToolCall
        for streaming in (False,True):
            with self.subTest(streaming=streaming),isolated_agent([
                ModelResponse(tool_calls=[ToolCall('write_file',{'path':'a.txt','content':'one'}),
                                          ToolCall('write_file',{'path':'b.txt','content':'two'})]),
                ModelResponse(text='Must not be requested')]) as agent:
                effects=[]
                def interrupted_write(args):
                    effects.append(args)
                    raise RuntimeError('Synthetic failure after a possible mutation')
                agent.tools.get('write_file').handler=interrupted_write
                if streaming:
                    events=list(agent.respond_stream('Выполни обработку и покажи итог.'))
                    self.assertEqual(events[-1]['outcome'],'failed')
                else:agent.respond('Выполни обработку и покажи итог.')
                self.assertEqual(len(effects),1)
                self.assertEqual(len(agent.provider.inputs),1)
                self.assertEqual(agent.last_turn_outcome,'failed')

    def test_cancelled_tool_stops_the_batch_and_next_model_round(self):
        from tests.test_runtime_audit import isolated_agent
        from ai_agent.types import ModelResponse,ToolCall
        from ai_agent.tools.base import ToolResult
        for streaming in (False,True):
            with self.subTest(streaming=streaming),isolated_agent([
                ModelResponse(tool_calls=[ToolCall('read_file',{'path':'a.txt'}),
                                          ToolCall('write_file',{'path':'b.txt','content':'must not run'})]),
                ModelResponse(text='Must not be requested')]) as agent:
                agent.tools.get('read_file').handler=lambda args:ToolResult('cancelled',display_text='Cancelled fixture')
                if streaming:list(agent.respond_stream('Выполни обработку и покажи итог.'))
                else:agent.respond('Выполни обработку и покажи итог.')
                self.assertEqual(len(agent.provider.inputs),1)
                self.assertEqual(agent.last_turn_outcome,'cancelled')
                self.assertFalse((agent.files.workspace_dir/'b.txt').exists())

    @unittest.skipIf(sys.platform=='linux','Windows/unavailable host contract')
    def test_unsupported_platform_fails_closed(self):
        with TemporaryDirectory() as directory:
            handler = HostedHandler(ProcessHandlerSpec('host_process_fixture:run',Path(directory),code_root=ROOT/'tests'))
            result = handler({})
            self.assertEqual(result.status,'blocked')
            self.assertEqual(result.error.code,'host_unavailable')


@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').exists(),'requires Linux bubblewrap')
class IsolatedProcessHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.workspace = Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def registry(self,operation='echo',timeout=3,*,mutation=False):
        writable = operation in {'detached','detached_return','mutation','atomic'}
        handler = HostedHandler(ProcessHandlerSpec('host_process_fixture:run',self.workspace,
            configuration={'operation':operation},code_root=ROOT/'tests',read_only_workspace=not writable),timeout_seconds=timeout)
        registry = ToolRegistry()
        registry.register(Tool('host_fixture','Synthetic handler',{'type':'object','additionalProperties':True},handler,
            output_schema={'type':'object'},timeout_seconds=timeout,
            permissions=(ToolPermission('fixture.write' if writable else 'fixture.read','write' if writable else 'read'),),
            idempotent=mutation,retry_policy=RetryPolicy(max_attempts=3 if mutation else 1)))
        return registry

    def test_json_unicode_and_structured_result_round_trip(self):
        value = {'text':'Привет, Avelin '*10000,'nested':[1,False,None], 'entrypoint':'os:system'}
        result = self.registry().invoke('host_fixture',value)
        self.assertTrue(result.ok,result.text())
        self.assertEqual(result.output,value)
        self.assertEqual(self.registry('typed').invoke('host_fixture',{}).output,{'verified':'structured'})

    def test_filesystem_network_environment_and_import_path_are_isolated(self):
        secret = self.workspace.parent/(self.workspace.name+'-outside.txt')
        secret.write_text('PRIVATE')
        os.environ['AVELIN_HOST_TEST_SECRET']='must-not-cross-IPC'
        try:
            result = self.registry('probe').invoke('host_fixture',{'host_path':str(secret)})
            self.assertTrue(result.ok,result.text())
            self.assertEqual(result.output,{'outside':False,'network':False,'secret':None,'cwd_in_path':False})
        finally:
            secret.unlink()
            os.environ.pop('AVELIN_HOST_TEST_SECRET',None)

    def test_noise_error_and_invalid_output_do_not_corrupt_or_leak_ipc(self):
        self.assertEqual(self.registry('noisy').invoke('host_fixture',{}).output,{'ok':True})
        result = self.registry('secret_error').invoke('host_fixture',{})
        self.assertFalse(result.ok)
        self.assertNotIn('sk-fixture',result.text())
        self.assertFalse(self.registry('invalid_output').invoke('host_fixture',{}).ok)
        self.assertEqual(self.registry('typed_bad').invoke('host_fixture',{}).error.code,'host_protocol')

    def test_timeout_kills_ignoring_handler_and_detached_child_without_late_write(self):
        result = self.registry('detached',.5).invoke('host_fixture',{})
        self.assertEqual(result.error.code,'timeout')
        self.assertTrue((self.workspace/'ready.txt').exists())
        time.sleep(1)
        self.assertFalse((self.workspace/'late.txt').exists())

    def test_cancel_waits_for_real_process_exit(self):
        registry = self.registry('detached')
        cancellation = Event();results=[]
        worker = Thread(target=lambda:results.append(registry.invoke('host_fixture',{},cancellation=cancellation)))
        worker.start()
        try:
            deadline = time.monotonic()+3
            while not (self.workspace/'ready.txt').exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue((self.workspace/'ready.txt').exists())
            cancellation.set()
            worker.join(3)
        finally:
            cancellation.set();worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].status,'cancelled')
        time.sleep(1)
        self.assertFalse((self.workspace/'late.txt').exists())

    def test_success_does_not_leave_detached_children_running(self):
        result = self.registry('detached_return').invoke('host_fixture',{})
        self.assertTrue(result.ok,result.text())
        time.sleep(1)
        self.assertFalse((self.workspace/'late.txt').exists())

    def test_mutation_timeout_never_retries_even_if_marked_idempotent(self):
        result = self.registry('mutation',.5,mutation=True).invoke('host_fixture',{})
        self.assertEqual(result.error.code,'timeout')
        self.assertEqual(result.attempts,1)
        self.assertTrue(result.error.details['outcome_unknown'])
        self.assertEqual((self.workspace/'count.txt').read_text(),'1')
        time.sleep(.8)
        self.assertFalse((self.workspace/'late.txt').exists())

    def test_memory_failure_does_not_take_down_parent_or_next_invocation(self):
        self.assertFalse(self.registry('memory').invoke('host_fixture',{}).ok)
        self.assertEqual(self.registry().invoke('host_fixture',{'still':'alive'}).output,{'still':'alive'})

    def test_overlapping_writable_code_root_is_rejected(self):
        result = HostedHandler(ProcessHandlerSpec('fixture:run',self.workspace,code_root=self.workspace))({})
        self.assertEqual(result.error.code,'host_unavailable')

    def test_parent_permissions_block_execution_before_host_is_started(self):
        handler = self.registry('mutation').get('host_fixture').handler
        registry = ToolRegistry(granted_permissions=set())
        registry.register(Tool('denied_fixture','Fixture',{'type':'object'},handler,
            permissions=(ToolPermission('workspace.write','write'),)))
        self.assertEqual(registry.invoke('denied_fixture',{}).status,'blocked')
        self.assertFalse((self.workspace/'ready.txt').exists())

    def test_durable_remote_cancel_kills_host_and_keeps_service_usable(self):
        from tests.test_llm_automations import LLMAutomationTests,Provider
        from ai_agent.types import ModelResponse,ToolCall
        fixture = LLMAutomationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        workspace = fixture.service.user_workspace(fixture.user)
        workspace.mkdir(parents=True,exist_ok=True)
        fixture.handlers['web_search'] = HostedHandler(ProcessHandlerSpec('host_process_fixture:run',workspace,
            configuration={'operation':'detached'},code_root=ROOT/'tests',read_only_workspace=False),timeout_seconds=5)
        # The fixture search handler deliberately writes markers; declare that
        # permission when the factory registers it in the scoped registry.
        original_factory = fixture.service._automation_agent_factory
        def factory(*args):
            agent = original_factory(*args)
            agent.tools.get('web_search').permissions=(ToolPermission('workspace.write','write'),)
            return agent
        fixture.service._automation_agent_factory = factory
        fixture.provider = Provider([ModelResponse(tool_calls=[ToolCall('web_search',{'query':'fixture'})])])
        _,occurrence = fixture.occurrence()
        errors=[]
        def work():
            try:fixture.service.process_automation_queue()
            except Exception as exc:errors.append(exc)
        worker = Thread(target=work)
        worker.start()
        try:
            deadline = time.monotonic()+5
            while not (workspace/'ready.txt').exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue((workspace/'ready.txt').exists())
            stopped = fixture.service.database.request_task_stop(fixture.user,occurrence['task_run_id'],'cancelled')
            self.assertEqual(stopped['state'],'running')
            worker.join(5)
        finally:
            fixture.service.database.request_task_stop(fixture.user,occurrence['task_run_id'],'cancelled')
            worker.join(6)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(fixture.service.database.get_task_run(fixture.user,occurrence['task_run_id'])['state'],'cancelled')
        self.assertEqual(len(fixture.provider.inputs),1)
        time.sleep(1)
        self.assertFalse((workspace/'late.txt').exists())
        fixture.provider = Provider([ModelResponse(text='Новая задача выполнена.')])
        _,fresh = fixture.occurrence(prompt='Подготовь короткий учебный текст.')
        fixture.service.process_automation_queue()
        self.assertEqual(fixture.service.database.get_task_run(fixture.user,fresh['task_run_id'])['state'],'completed')

    def test_read_only_host_cannot_write_even_with_a_buggy_handler(self):
        handler = HostedHandler(ProcessHandlerSpec('host_process_fixture:run',self.workspace,
            configuration={'operation':'mutation'},code_root=ROOT/'tests'))
        result = handler({})
        self.assertFalse(result.ok)
        self.assertFalse((self.workspace/'ready.txt').exists())

    def test_writable_host_requires_declared_parent_permission(self):
        handler = HostedHandler(ProcessHandlerSpec('host_process_fixture:run',self.workspace,
            code_root=ROOT/'tests',read_only_workspace=False))
        with self.assertRaises(ValueError):
            ToolRegistry().register(Tool('bad_declaration','Fixture',{'type':'object'},handler))

    def test_long_task_mount_preserves_paths_but_hides_other_jobs(self):
        from hashlib import sha256
        prefix = 'outputs/tasks/alpha'
        job = self.workspace/prefix
        job.mkdir(parents=True)
        (job/'own.txt').write_text('own')
        other = self.workspace/'outputs/tasks/beta'
        other.mkdir()
        (other/'private.txt').write_text('PRIVATE')
        handler = HostedHandler(ProcessHandlerSpec('ai_agent.tools.hosted_files:call_file_tool',job,
            configuration={'name':'file_metadata'},workspace_subpath=prefix))
        result = handler({'path':prefix+'/own.txt'})
        self.assertEqual(result['path'],prefix+'/own.txt')
        self.assertEqual(result['sha256'],sha256(b'own').hexdigest())
        probe = HostedHandler(ProcessHandlerSpec('host_process_fixture:run',job,
            configuration={'operation':'probe'},code_root=ROOT/'tests',workspace_subpath=prefix))
        self.assertFalse(probe({'host_path':'/workspace/outputs/tasks/beta/private.txt'})['outside'])

    def test_forced_stop_cannot_publish_partial_replacement(self):
        target = self.workspace/'original.txt'
        target.write_text('ORIGINAL')
        result = self.registry('atomic',.5).invoke('host_fixture',{})
        self.assertEqual(result.error.code,'timeout')
        self.assertEqual(target.read_text(),'ORIGINAL')
        self.assertTrue((self.workspace/'ready.txt').exists())
        time.sleep(.8)
        self.assertEqual(target.read_text(),'ORIGINAL')

    def test_actual_text_docx_and_pptx_tools_use_host_and_publish_valid_files(self):
        from tests.test_runtime_audit import isolated_agent
        from ai_agent.tools.hosted_files import HostedFileHandler
        from zipfile import ZipFile
        with isolated_agent([]) as agent:
            calls = [('write_file',{'path':'note.txt','content':'Привет'}),
                ('create_docx',{'path':'report.docx','paragraphs':['Original']}),
                ('append_docx',{'path':'report.docx','paragraphs':['Appended']}),
                ('create_presentation',{'path':'slides.pptx','title':'Fixture'})]
            for name,arguments in calls:
                self.assertIsInstance(agent.tools.get(name).handler,HostedFileHandler)
                result = agent.tools.invoke(name,arguments)
                self.assertTrue(result.ok,result.text())
            self.assertEqual((agent.files.workspace_dir/'note.txt').read_text(),'Привет')
            with ZipFile(agent.files.workspace_dir/'report.docx') as package:
                text = package.read('word/document.xml').decode()
                self.assertIn('Original',text);self.assertIn('Appended',text)
                self.assertIsNone(package.testzip())
            with ZipFile(agent.files.workspace_dir/'slides.pptx') as package:self.assertIsNone(package.testzip())


if __name__=='__main__':unittest.main()
