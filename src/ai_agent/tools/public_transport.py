"""Pin every outgoing socket to the addresses validated for that connection."""
import http.client
import ipaddress
import socket
from urllib import request


class AddressBlocked(ValueError):
    pass


def connect_public(address, timeout=15, source_address=None, *, allow_private=False):
    host, port = address
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError('DNS returned no addresses')
    for _, _, _, _, endpoint in addresses:
        ip = ipaddress.ip_address(endpoint[0])
        mapped = getattr(ip, 'ipv4_mapped', None)
        if not allow_private and (not ip.is_global or ip.is_multicast or (mapped and not mapped.is_global)):
            raise AddressBlocked('Non-public destination address blocked.')
    failure = None
    for family, kind, protocol, _, endpoint in addresses:
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(endpoint)
            return connection
        except OSError as exc:
            connection.close()
            failure = exc
    raise failure or OSError('Could not connect to validated destination')


class PinnedHTTPHandler(request.HTTPHandler):
    def __init__(self, allow_private=False):
        super().__init__()
        self.allow_private = allow_private

    def http_open(self, req):
        def connection(host, **kwargs):
            client = http.client.HTTPConnection(host, **kwargs)
            client._create_connection = lambda address, timeout, source_address=None: connect_public(
                address, timeout, source_address, allow_private=self.allow_private)
            return client
        return self.do_open(connection, req)


class PinnedHTTPSHandler(request.HTTPSHandler):
    def __init__(self, allow_private=False):
        super().__init__()
        self.allow_private = allow_private

    def https_open(self, req):
        def connection(host, **kwargs):
            client = http.client.HTTPSConnection(host, **kwargs)
            client._create_connection = lambda address, timeout, source_address=None: connect_public(
                address, timeout, source_address, allow_private=self.allow_private)
            return client
        return self.do_open(connection, req, context=self._context)
