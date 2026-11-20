"""Fixed public GET/HEAD transport broker with a killable process lifetime.

Only this trusted transport receives the host network. Generic HostedHandlers,
generated code, and file workers retain their network namespace. The broker
uses the existing URL/redirect/header validation and DNS-pinned connections;
it has an empty read-only workspace and no live clients. The public HTTP profile
has no provider credentials; the separate fixed search profile passes only its
required fallback credential through private stdin.
"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from ai_agent.tools.base import ToolError,ToolResult
from ai_agent.tools.process_host import HostedHandler,ProcessHandlerSpec


class _HTTPBroker(HostedHandler):
    """Trusted egress boundary, never a network option for generic workers.

    Production constructs only fixed public HTTP/search transport entry points. This
    broker itself is trusted to validate and pin every outgoing connection;
    its network isolation is not a kernel destination firewall. No caller
    supplied module, client, workspace or private-network flag is accepted.
    """
    def _command(self,roots):
        command=super()._command(roots)
        options=['--share-net']
        for path in ('/etc/resolv.conf','/etc/hosts','/etc/nsswitch.conf','/etc/ssl/certs'):
            if Path(path).exists():options+=['--ro-bind',path,path]
        boundary=command.index('--')
        return command[:boundary]+options+command[boundary:]


class HostedHTTPHandler:
    requires_workspace_write=False

    def __init__(self,http,*,timeout_seconds=30):
        self.http,self.timeout_seconds=http,timeout_seconds

    def __call__(self,arguments,context=None):
        if self.http.allow_private_networks:
            message='Public HTTP broker не поддерживает доступ к частной сети.'
            return ToolResult('blocked',error=ToolError('private_network_disabled',message),display_text=message)
        with TemporaryDirectory(prefix='avelin-http-') as folder:
            spec=ProcessHandlerSpec('ai_agent.tools.hosted_http:call_public_http',Path(folder),
                configuration={'socket_timeout':min(self.http.timeout_seconds,self.timeout_seconds),
                    'max_response_chars':self.http.max_response_chars},expose_value_errors=True)
            return _HTTPBroker(spec,timeout_seconds=self.timeout_seconds)(arguments,context)

def call_public_http(arguments,configuration):
    from ai_agent.tools.http import HttpSandbox
    http=HttpSandbox(timeout_seconds=configuration['socket_timeout'],
        max_response_chars=configuration['max_response_chars'],allow_private_networks=False)
    return http.request(url=str(arguments.get('url','')),method=str(arguments.get('method','GET')),
        headers=arguments.get('headers') if isinstance(arguments.get('headers'),dict) else None)


def enable_http_process_host(registry,http):
    if sys.platform!='linux':return
    tool=registry.get('http_request')
    tool.handler=HostedHTTPHandler(http,timeout_seconds=tool.timeout_seconds)
