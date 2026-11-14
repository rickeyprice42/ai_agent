from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib import request
from unittest.mock import patch, MagicMock
import base64
import socket
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_agent.tools.public_transport import connect_public, AddressBlocked
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.egress_proxy import BrowserEgressProxy
from ai_agent.tools.browser import PlaywrightBrowserRunner, BrowserFailure
from tempfile import TemporaryDirectory


class PublicTransportTests(unittest.TestCase):
    def test_browser_proxy_rechecks_dns_before_connect(self):
        with TemporaryDirectory() as folder:
            browser = PlaywrightBrowserRunner(Path(folder), timeout_seconds=5)
            if not browser.availability()[0]:
                self.skipTest('Requires installed Chromium/Edge')
            original = socket.getaddrinfo
            pinned_checks = []
            def resolve(host, port, *args, **kwargs):
                if host == 'rebind.test':
                    connecting = kwargs.get('type') == socket.SOCK_STREAM
                    if connecting:
                        pinned_checks.append(True)
                    return original('127.0.0.1' if connecting else '93.184.216.34', port, *args, **kwargs)
                return original(host, port, *args, **kwargs)
            with patch('socket.getaddrinfo', side_effect=resolve):
                try:
                    result = browser.read(url='http://rebind.test/', screenshot=False)
                    self.assertEqual(result['status'], 403)
                except BrowserFailure:
                    pass
            self.assertTrue(pinned_checks, 'Browser must use the pinned transport proxy')

    def test_connection_uses_numeric_validated_endpoint_without_second_lookup(self):
        endpoint = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        connection = MagicMock()
        with patch('socket.getaddrinfo', side_effect=[[endpoint], AssertionError('DNS resolved twice')]) as resolve, patch('socket.socket', return_value=connection):
            self.assertIs(connect_public(('rebind.test', 80)), connection)
        self.assertEqual(resolve.call_count, 1)
        connection.connect.assert_called_once_with(('93.184.216.34', 80))

    def test_mixed_and_mapped_private_answers_block_before_connection(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '100.64.0.1', '::ffff:127.0.0.1', '224.0.0.1'):
            with self.subTest(address=address), patch('socket.getaddrinfo', return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', (address, 80))
            ]), patch('socket.socket') as connect:
                with self.assertRaises(AddressBlocked):
                    connect_public(('rebind.test', 80))
                connect.assert_not_called()

    def test_fetch_blocks_rebinding_before_contacting_private_service(self):
        original = socket.getaddrinfo
        def resolve(host, port, *args, **kwargs):
            if host == 'rebind.test':
                return original('93.184.216.34' if port is None else '127.0.0.1', port, *args, **kwargs)
            return original(host, port, *args, **kwargs)
        with patch('socket.getaddrinfo', side_effect=resolve), patch('socket.socket') as connect:
            with self.assertRaises(AddressBlocked):
                HttpSandbox().fetch('http://rebind.test/sentinel')
            connect.assert_not_called()

    def test_proxy_requires_auth_and_validates_public_destination(self):
        with BrowserEgressProxy(lambda host: True) as config:
            opener = request.build_opener(request.ProxyHandler({'http': config['server']}))
            with self.assertRaises(request.HTTPError) as failure:
                opener.open('http://example.test/', timeout=3)
            self.assertEqual(failure.exception.code, 407)
            failure.exception.close()
            authorization = 'Basic ' + base64.b64encode(f'{config["username"]}:{config["password"]}'.encode()).decode()
            # Literal private target is blocked even with valid proxy credentials.
            req = request.Request('http://127.0.0.2/', headers={'Proxy-Authorization': authorization})
            with patch('urllib.request.proxy_bypass', return_value=False):
                with self.assertRaises(request.HTTPError) as blocked:
                    opener.open(req, timeout=3)
            self.assertEqual(blocked.exception.code, 403)
            blocked.exception.close()

    def test_normal_http_and_proxy_preserve_host_and_strip_proxy_credentials(self):
        observations = []
        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                observations.append(dict(self.headers))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'OK')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/'
        try:
            self.assertEqual(HttpSandbox(allow_private_networks=True).fetch(url)['body'], 'OK')
            with BrowserEgressProxy(lambda host: True, allow_private=True) as config:
                authorization = 'Basic ' + base64.b64encode(f'{config["username"]}:{config["password"]}'.encode()).decode()
                opener = request.build_opener(request.ProxyHandler({'http': config['server']}))
                with patch('urllib.request.proxy_bypass', return_value=False):
                    with opener.open(request.Request(url, headers={'Proxy-Authorization': authorization}), timeout=3) as response:
                        self.assertEqual(response.read(), b'OK')
            self.assertEqual(len(observations), 2)
            self.assertTrue(all('Proxy-Authorization' not in headers for headers in observations))
            self.assertEqual(observations[-1]['Host'], f'127.0.0.1:{server.server_port}')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
