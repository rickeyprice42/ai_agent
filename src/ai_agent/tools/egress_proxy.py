"""Per-browser authenticated proxy with DNS-pinned public connections."""
from base64 import b64encode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe, compare_digest
from threading import Thread, BoundedSemaphore, Lock
from time import monotonic
from urllib.parse import urlsplit
import select
import socket

from ai_agent.tools.public_transport import connect_public, AddressBlocked


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def process_request(self, connection, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(connection)
            return
        try:
            super().process_request(connection, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, connection, address):
        with self.lock:
            self.connections.add(connection)
        try:
            super().process_request_thread(connection, address)
        finally:
            with self.lock:
                self.connections.discard(connection)
            self.slots.release()


class _ProxyHandler(BaseHTTPRequestHandler):
    timeout = 10
    rbufsize = 0

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self._forward(tunnel=True)

    def do_GET(self):
        self._forward(tunnel=False)

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET

    def _forward(self, *, tunnel):
        self.close_connection = True
        if not compare_digest(self.headers.get('Proxy-Authorization', '').encode('latin-1'),
                              self.server.authorization.encode('ascii')):
            self.send_response(407)
            self.send_header('Proxy-Authenticate', 'Basic realm="Avelin egress"')
            self.end_headers()
            return
        try:
            parsed = urlsplit('https://' + self.path if tunnel else self.path)
            if parsed.scheme != ('https' if tunnel else 'http') or not parsed.hostname or parsed.username or parsed.password:
                raise AddressBlocked('Invalid proxy destination')
            host = parsed.hostname
            if not self.server.allowed_host(host):
                raise AddressBlocked('Host outside browser allowlist')
            upstream = connect_public((host, parsed.port or (443 if tunnel else 80)),
                timeout=self.server.timeout_seconds, allow_private=self.server.allow_private)
        except (ValueError, OSError):
            self.send_error(403, 'Destination blocked or unavailable')
            return
        with upstream:
            with self.server.lock:
                self.server.connections.add(upstream)
            try:
                if tunnel:
                    self.send_response(200, 'Connection established')
                    self.end_headers()
                else:
                    path = parsed.path or '/'
                    if parsed.query:
                        path += '?' + parsed.query
                    headers = [f'{self.command} {path} HTTP/1.1', f'Host: {parsed.netloc}', 'Connection: close']
                    headers += [f'{name}: {value}' for name, value in self.headers.items()
                                if name.lower() not in ('proxy-authorization', 'proxy-connection', 'connection', 'host')]
                    upstream.sendall(('\r\n'.join(headers) + '\r\n\r\n').encode('latin-1'))
                deadline = monotonic() + 300
                peers = (self.connection, upstream)
                while monotonic() < deadline:
                    readable, _, _ = select.select(peers, [], [], 1)
                    for origin in readable:
                        data = origin.recv(65536)
                        if not data:
                            return
                        destination = upstream if origin is self.connection else self.connection
                        destination.sendall(data)
            except OSError:
                return
            finally:
                with self.server.lock:
                    self.server.connections.discard(upstream)


class BrowserEgressProxy:
    def __init__(self, allowed_host, *, allow_private=False, timeout_seconds=15):
        self.server = _ProxyServer(('127.0.0.1', 0), _ProxyHandler)
        self.username = 'avelin'
        self.password = token_urlsafe(32)
        self.server.authorization = 'Basic ' + b64encode(f'{self.username}:{self.password}'.encode()).decode()
        self.server.allowed_host = allowed_host
        self.server.allow_private = allow_private
        self.server.timeout_seconds = timeout_seconds
        self.server.connections = set()
        self.server.lock = Lock()
        self.server.slots = BoundedSemaphore(32)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return {'server': f'http://127.0.0.1:{self.server.server_port}',
                'username': self.username, 'password': self.password, 'bypass': '<-loopback>'}

    def __exit__(self, *args):
        self.server.shutdown()
        with self.server.lock:
            for connection in self.server.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.server.server_close()
        self.thread.join(timeout=2)
