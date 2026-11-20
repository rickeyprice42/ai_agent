"""Killable browser tree: trusted egress broker outside a networkless browser."""
from base64 import b64decode, b64encode
from contextlib import AbstractContextManager
from pathlib import Path
from socketserver import BaseRequestHandler
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic, sleep
from urllib.parse import urlparse
import os
import json
from importlib.util import find_spec
import select
import socket
import sys

from ai_agent.tools.atomic_files import atomic_output
from ai_agent.tools.base import CURRENT_EXECUTION, ToolExecutionContext, ToolResult
from ai_agent.tools.browser import (BrowserFailure, PlaywrightBrowserRunner, _linux_browser_executable,
                                    _host_allowed, _catalog_resource_allowed)
from ai_agent.tools.egress_proxy import BrowserEgressProxy, _ProxyServer
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.process_host import HostedHandler, ProcessHandlerSpec

MAX_SCREENSHOT_BYTES = 900_000


def _installed_executable():
    configured = _linux_browser_executable()
    if configured is not None: return configured
    package = find_spec('playwright')
    if package is None or not package.origin: return None
    driver = Path(package.origin).parent / 'driver' / 'package'
    try:
        browsers = json.loads((driver / 'browsers.json').read_text())['browsers']
        revision = next(item['revision'] for item in browsers if item['name'] == 'chromium')
    except (OSError, ValueError, KeyError, StopIteration): return None
    location = os.getenv('PLAYWRIGHT_BROWSERS_PATH', '')
    root = (driver / '.local-browsers' if location == '0' else Path(location).expanduser()
            if location else Path(os.getenv('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'ms-playwright')
    for platform in ('chrome-linux64', 'chrome-linux', 'chrome-linux-arm64'):
        executable = root / f'chromium-{revision}' / platform / 'chrome'
        if executable.is_file(): return executable.resolve()
    return None


def _browser_command(command):
    return [part.replace('/tools/process_worker.py', '/tools/browser_worker.py') for part in command]


class _NetworklessBrowser(HostedHandler):
    def _command(self, roots):
        command = _browser_command(super()._command(roots))
        options = ['--ro-bind', self.spec.configuration['browser_root'], '/avelin_browser',
                   '--ro-bind', self.spec.configuration['bridge_root'], '/egress']
        for path in ('/etc/ssl/certs', '/etc/fonts'):
            if Path(path).exists(): options += ['--ro-bind', path, path]
        boundary = command.index('--')
        return command[:boundary] + options + command[boundary:]


class _CombinedCancel:
    def __init__(self, stop, parent): self.stop, self.parent = stop, parent
    def is_set(self): return self.stop.is_set() or (self.parent is not None and self.parent.cancelled)


def _execute_pair(arguments, configuration, timeout):
    started = monotonic()
    parent = CURRENT_EXECUTION.get()
    stop, done = Event(), Event()
    context = ToolExecutionContext(_CombinedCancel(stop, parent))
    with TemporaryDirectory(prefix='avelin-egress-') as bridge, TemporaryDirectory(prefix='avelin-browser-') as folder:
        proxy_spec = ProcessHandlerSpec('ai_agent.tools.hosted_browser:call_browser_proxy', Path(bridge),
            configuration={key: configuration[key] for key in ('allowed_hosts', 'timeout_seconds')},
            read_only_workspace=False)
        box = {}
        def proxy():
            try: box['result'] = _HTTPBroker(proxy_spec, timeout_seconds=timeout)(arguments, context)
            finally: done.set()
        thread = Thread(target=proxy)
        thread.start()
        try:
            ready = Path(bridge) / 'ready.json'
            while not ready.exists():
                if context.cancelled: raise BrowserFailure('cancelled', 'Браузер остановлен.')
                if done.is_set():
                    result = box.get('result')
                    if isinstance(result, dict) and 'failure' in result: raise BrowserFailure(**result['failure'])
                    if isinstance(result, ToolResult) and result.error:
                        raise BrowserFailure(result.error.code, result.text(), False, result.error.details)
                    raise BrowserFailure('host_failed', 'Сетевой процесс браузера не запустился.')
                if monotonic() - started >= timeout: raise BrowserFailure('timeout', 'Превышен срок запуска браузера.')
                sleep(.02)
            proxy_config = json.loads(ready.read_text())
            spec = ProcessHandlerSpec('ai_agent.tools.hosted_browser:call_browser_inner', Path(folder),
                configuration={**configuration, **proxy_config, 'bridge_root': bridge})
            return _NetworklessBrowser(spec, timeout_seconds=max(.01, timeout - (monotonic() - started)))(arguments, context)
        finally:
            stop.set()
            thread.join()


class HostedBrowserBackend:
    def __init__(self, runner, *, timeout_seconds):
        self.runner, self.timeout_seconds = runner, timeout_seconds

    def availability(self):
        return (True, None) if _installed_executable() else (False, 'Установленный Chromium недоступен.')

    def __call__(self, **arguments):
        runner = self.runner
        if runner.allow_private_networks:
            raise BrowserFailure('private_network_disabled', 'Изолированный браузер разрешает только публичную сеть.')
        target = runner._screenshot_path(arguments.get('screenshot_path')) if arguments.get('screenshot') else None
        executable = _installed_executable()
        if executable is None:
            raise BrowserFailure('browser_unavailable', 'Установленный Chromium недоступен.')
        configuration = {'browser_root': str(executable.resolve().parent), 'browser_name': executable.name,
            'allowed_hosts': list(runner.allowed_hosts), 'timeout_seconds': runner.timeout_ms / 1000,
            'max_text_chars': runner.max_text_chars, 'max_elements': runner.max_elements}
        frame = _execute_pair(arguments, configuration, self.timeout_seconds)
        if isinstance(frame, ToolResult):
            error = frame.error
            raise BrowserFailure(error.code if error else 'host_failed', frame.text(), False,
                {**(error.details if error else {}), **({'outcome_unknown': True} if arguments.get('actions') else {})})
        if not isinstance(frame, dict): raise BrowserFailure('host_protocol', 'Браузер вернул некорректный результат.')
        if 'failure' in frame:
            failure = dict(frame['failure'])
            if arguments.get('actions') and failure.get('code') in {'timeout', 'browser_error', 'host_output_limit'}:
                failure['details'] = {**(failure.get('details') or {}), 'outcome_unknown': True}
                failure['retryable'] = False
            raise BrowserFailure(**failure)
        result = frame.get('result')
        if not isinstance(result, dict): raise BrowserFailure('host_protocol', 'Браузер не вернул snapshot.')
        context = CURRENT_EXECUTION.get()
        if context is not None and context.cancelled: raise BrowserFailure('cancelled', 'Браузер остановлен.')
        screenshot = frame.get('screenshot_png')
        if screenshot is not None:
            if target is None: raise BrowserFailure('host_protocol', 'Получен незапрошенный screenshot.')
            try: raw = b64decode(screenshot, validate=True)
            except (ValueError, TypeError): raise BrowserFailure('host_protocol', 'Некорректный screenshot.') from None
            if len(raw) > MAX_SCREENSHOT_BYTES or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                raise BrowserFailure('host_output_limit', 'Screenshot превышает предел результата.')
            with atomic_output(target, overwrite=True) as temporary: temporary.write_bytes(raw)
            result['screenshot'] = runner._artifact(target)
        return result


def call_browser_proxy(arguments, configuration):
    # Validate the initial URL here, then pin every actual connection at proxy.
    bridge = Path('/workspace')
    runner = PlaywrightBrowserRunner(bridge, allowed_hosts=tuple(configuration['allowed_hosts']))
    try:
        initial = runner._validate_url(str(arguments.get('url', '')))
        host = initial.hostname.casefold()
        if runner.allowed_hosts and not _host_allowed(host, runner.allowed_hosts):
            raise BrowserFailure('host_not_allowed', 'Хост не входит в browser allowlist.')
        hosts = runner.allowed_hosts or (host,)
        with BrowserEgressProxy(lambda value: _host_allowed(value, hosts) or
                _catalog_resource_allowed(host, value, 'GET', False),
                timeout_seconds=configuration['timeout_seconds'],
                unix_socket=bridge / 'proxy.sock') as proxy:
            with atomic_output(bridge / 'ready.json', overwrite=False) as pending:
                pending.write_text(json.dumps({'proxy_user': proxy['username'], 'proxy_password': proxy['password']}))
            while True: sleep(.1)
    except BrowserFailure as exc:
        return {'failure': {'code': exc.code, 'message': exc.message,
                            'retryable': exc.retryable, 'details': exc.details}}


class _RelayHandler(BaseRequestHandler):
    def handle(self):
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream.settimeout(10)
        with upstream:
            upstream.connect('/egress/proxy.sock')
            with self.server.lock: self.server.connections.add(upstream)
            try:
                while True:
                    readable, _, _ = select.select((self.request, upstream), [], [], .5)
                    for origin in readable:
                        data = origin.recv(65536)
                        if not data: return
                        (upstream if origin is self.request else self.request).sendall(data)
            except OSError:
                return
            finally:
                with self.server.lock: self.server.connections.discard(upstream)


class _UnixRelay(AbstractContextManager):
    def __init__(self, configuration):
        self.configuration = configuration
        self.server = _ProxyServer(('127.0.0.1', 0), _RelayHandler)
        self.server.slots = BoundedSemaphore(32)
        self.server.lock = Lock()
        self.server.connections = set()
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return {'server': f'http://127.0.0.1:{self.server.server_port}', 'bypass': '<-loopback>',
                'username': self.configuration['proxy_user'], 'password': self.configuration['proxy_password']}

    def __exit__(self, *args):
        self.server.shutdown()
        with self.server.lock:
            for connection in self.server.connections:
                try: connection.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        self.server.server_close()
        self.thread.join(2)


class _BrokeredRunner(PlaywrightBrowserRunner):
    def _validate_url(self, value, validated_hosts=None):
        # DNS is unavailable here. Real destinations are resolved, validated
        # and pinned by the external proxy for every connection.
        parsed = urlparse(value.strip())
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise BrowserFailure('invalid_url', 'Browser URL должен быть публичным http/https URL.')
        if parsed.username or parsed.password:
            raise BrowserFailure('credentials_forbidden', 'Credentials в browser URL запрещены.')
        return parsed


def call_browser_inner(arguments, configuration):
    os.environ['AVELIN_BROWSER_EXECUTABLE'] = '/avelin_browser/' + configuration['browser_name']
    with TemporaryDirectory(prefix='browser-profile-') as folder:
        runner = _BrokeredRunner(Path(folder), timeout_seconds=configuration['timeout_seconds'],
            allowed_hosts=tuple(configuration['allowed_hosts']), max_text_chars=configuration['max_text_chars'],
            max_elements=configuration['max_elements'])
        runner._proxy_factory = lambda *args, **kwargs: _UnixRelay(configuration)
        try:
            result = runner._run(url=arguments['url'], actions=arguments.get('actions', []),
                expected_dom_hash=arguments.get('expected_dom_hash'), screenshot=arguments.get('screenshot', False),
                screenshot_path='capture.png', screenshot_full_page=arguments.get('screenshot_full_page', True))
            frame = {'result': result}
            if result.get('screenshot'):
                raw = (Path(folder) / 'capture.png').read_bytes()
                if len(raw) > MAX_SCREENSHOT_BYTES:
                    raise BrowserFailure('host_output_limit', 'Screenshot превышает предел результата.')
                frame['screenshot_png'] = b64encode(raw).decode('ascii')
                result['screenshot'] = None
            return frame
        except BrowserFailure as exc:
            return {'failure': {'code': exc.code, 'message': exc.message,
                                'retryable': exc.retryable, 'details': exc.details}}


def enable_browser_process_host(registry, runner):
    if sys.platform != 'linux': return
    runner.process_backend = HostedBrowserBackend(runner,
        timeout_seconds=max(registry.get('browser_read').timeout_seconds,
                            registry.get('browser_interact').timeout_seconds))
