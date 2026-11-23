from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event,Thread
from unittest.mock import patch
import os,socketserver,sys,time,unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ai_agent.tools.hosted_http import HostedHTTPHandler, _HTTPBroker,enable_http_process_host
from ai_agent.tools.process_host import ProcessHandlerSpec
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.base import Tool,ToolRegistry,ToolExecutionContext


class HTTPHostContracts(unittest.TestCase):
    def test_private_network_setting_never_enables_the_public_broker(self):
        handler=HostedHTTPHandler(HttpSandbox(allow_private_networks=True),timeout_seconds=1)
        result=handler({'url':'http://127.0.0.1'})
        self.assertEqual(result.status,'blocked')

    def test_windows_registration_preserves_existing_handler(self):
        registry=ToolRegistry();handler=lambda args:'legacy'
        registry.register(Tool('http_request','HTTP',{'type':'object'},handler))
        with patch('ai_agent.tools.hosted_http.sys.platform','win32'):
            enable_http_process_host(registry,HttpSandbox())
        self.assertIs(registry.get('http_request').handler,handler)


@unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/bwrap').exists(),'requires Linux bubblewrap')
class HTTPHostLinux(unittest.TestCase):
    def test_mcp_factory_reads_json_sse_and_terminates_hung_worker(self):
        from dataclasses import replace
        from ai_agent.connectors import ConnectorDescriptor, ConnectorRegistry, McpClient
        from ai_agent.tools import hosted_mcp

        def fixture(spec, *, timeout_seconds):
            return _HTTPBroker(replace(spec, entrypoint="http_host_fixture:run", code_root=ROOT / "tests",
                configuration={**spec.configuration, "operation": "mcp"}), timeout_seconds=timeout_seconds)

        registry = ToolRegistry()
        with patch.object(hosted_mcp, "_HTTPBroker", side_effect=fixture) as broker:
            client = McpClient.for_https(ConnectorDescriptor("demo", "Demo", "Fixture"),
                                         "https://example.com/mcp", timeout_seconds=1)
            ConnectorRegistry(registry).register(client)
            self.assertTrue(registry.invoke("connector_demo_lookup", {"query": "ok"}).ok)
            before = broker.call_count
            started = time.monotonic()
            result = registry.invoke("connector_demo_lookup", {"query": "hang"})
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(broker.call_count - before, 1)
            self.assertTrue(result.error.details["outcome_unknown"])
            self.assertFalse(result.error.retryable)

    def test_vision_request_crosses_real_process_with_large_body(self):
        from dataclasses import replace
        from ai_agent.tools import hosted_vision

        def fixture(spec, *, timeout_seconds):
            return _HTTPBroker(replace(spec, entrypoint="http_host_fixture:run",
                code_root=ROOT / "tests", configuration={**spec.configuration, "operation": "vision"}),
                timeout_seconds=timeout_seconds)

        with patch.object(hosted_vision, "_HTTPBroker", side_effect=fixture):
            response = hosted_vision.hosted_vision_request(
                "https://api.openai.com/v1/responses", {"image": "a" * 2_100_000}, 5,
                {"Authorization": "Bearer synthetic"}, provider="fixture",
            )
        self.assertEqual(response, {"output_text": "Synthetic image description"})

    def fixture(self,workspace,operation,timeout=3):
        return _HTTPBroker(ProcessHandlerSpec('http_host_fixture:run',workspace,
            code_root=ROOT/'tests',configuration={'operation':operation},expose_value_errors=True),timeout_seconds=timeout)

    def test_transport_has_no_keys_database_user_workspace_or_inherited_environment(self):
        with TemporaryDirectory() as directory,patch.dict(os.environ,{'AVELIN_TEST_SECRET':'synthetic'}):
            result=self.fixture(Path(directory),'boundary')({})
        self.assertEqual(result,{'secret_env':None,'provider_keys':False,'database':False,'workspace_files':[]})

    def test_fixed_http_profile_blocks_private_targets_headers_methods_and_import_injection(self):
        handler=HostedHTTPHandler(HttpSandbox(),timeout_seconds=3)
        for args in ({'url':'http://127.0.0.1/'},{'url':'http://169.254.169.254/latest/meta-data/'},
                {'url':'http://[::ffff:127.0.0.1]/'},{'url':'file:///etc/passwd'},
                {'url':'http://user:password@example.com/'},
                {'url':'http://1.1.1.1/','method':'POST'},
                {'url':'http://1.1.1.1/','headers':{'Authorization':'synthetic'}},
                {'url':'http://127.0.0.1/','entrypoint':'os:system','allow_private_networks':True}):
            with self.subTest(args=args):
                result=handler(args);self.assertFalse(result.ok,result)
                self.assertEqual(result.error.code,'invalid_operation')

    def test_rebinding_is_blocked_inside_fresh_broker(self):
        with TemporaryDirectory() as directory:
            result=self.fixture(Path(directory),'rebind')({'port':12345})
        self.assertFalse(result.ok)
        self.assertIn('Non-public',result.text())

    def test_cancel_kills_detached_network_descendant_before_terminal(self):
        ready=Event();messages=[]
        class Receiver(socketserver.BaseRequestHandler):
            def handle(self):
                data=self.request.recv(100);messages.append(data)
                if data==b'CHILD_READY':ready.set()
        with socketserver.TCPServer(('127.0.0.1',0),Receiver) as server,TemporaryDirectory() as directory:
            serving=Thread(target=server.serve_forever,daemon=True);serving.start()
            cancel=Event();box={}
            broker=self.fixture(Path(directory),'late_network',timeout=5)
            worker=Thread(target=lambda:box.setdefault('result',broker({'port':server.server_address[1]},
                ToolExecutionContext(cancel))))
            worker.start()
            try:
                self.assertTrue(ready.wait(3),'Fixture descendant never started')
                cancel.set();worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(box['result'].status,'cancelled')
                time.sleep(1.2);self.assertNotIn(b'LATE',messages)
            finally:
                cancel.set();worker.join(5);server.shutdown();serving.join(2)

    def test_timeout_kills_network_descendant_and_returns_finite_result(self):
        ready=Event();messages=[]
        class Receiver(socketserver.BaseRequestHandler):
            def handle(self):
                data=self.request.recv(100);messages.append(data)
                if data==b'CHILD_READY':ready.set()
        with socketserver.TCPServer(('127.0.0.1',0),Receiver) as server,TemporaryDirectory() as directory:
            serving=Thread(target=server.serve_forever,daemon=True);serving.start();box={}
            broker=self.fixture(Path(directory),'late_network',timeout=2)
            worker=Thread(target=lambda:box.setdefault('result',broker({'port':server.server_address[1],'delay':3.5})))
            worker.start()
            try:
                self.assertTrue(ready.wait(1.8),'Fixture descendant never started')
                worker.join(4);self.assertFalse(worker.is_alive())
                self.assertEqual(box['result'].error.code,'timeout')
                time.sleep(1.8);self.assertNotIn(b'LATE',messages)
            finally:
                worker.join(5);server.shutdown();serving.join(2)




if __name__=='__main__':unittest.main()
