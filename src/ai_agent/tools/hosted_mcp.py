"""Fixed Linux worker for MCP POST requests to a configured HTTPS endpoint."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from urllib import request
from urllib.parse import urlsplit
from uuid import uuid4

from ai_agent.connectors import ConnectorFailure
from ai_agent.tools.base import ToolExecutionContext
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.hosted_search import _NoSearchRedirect
from ai_agent.tools.process_host import ProcessHandlerSpec
from ai_agent.tools.public_transport import PinnedHTTPSHandler

MAX_BODY = 1_000_000


def validate_endpoint(endpoint):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query):
        raise ValueError("MCP endpoint must be a configured HTTPS URL without credentials, query or fragment.")


class HostedMcpRequest:
    def __init__(self, endpoint: str, *, bearer_token: str = ""):
        validate_endpoint(endpoint)
        self.endpoint = endpoint
        self.bearer_token = bearer_token
        self.session_id = None
        self.protocol_version = None
        self.lock = Lock()

    def __call__(self, method, params, timeout, cancellation):
        deadline = monotonic() + timeout
        while not cancellation.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            if self.lock.acquire(timeout=min(.05, remaining)):
                try:
                    remaining = deadline - monotonic()
                    if not cancellation.is_set() and remaining > 0:
                        return self._request(method, params, remaining, cancellation)
                    break
                finally:
                    self.lock.release()
        raise ConnectorFailure("unavailable", "MCP request cancelled or timed out.")

    def _request(self, method, params, timeout, cancellation):
        if method == "initialize":
            self.session_id = self.protocol_version = None
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not method.startswith("notifications/"):
            message["id"] = uuid4().hex
        headers = {}
        if self.bearer_token:
            headers["Authorization"] = "Bearer " + self.bearer_token
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        with TemporaryDirectory(prefix="avelin-mcp-") as folder:
            spec = ProcessHandlerSpec("ai_agent.tools.hosted_mcp:call_mcp_post", Path(folder),
                configuration={"endpoint": self.endpoint, "headers": headers, "timeout": timeout})
            frame = _HTTPBroker(spec, timeout_seconds=timeout)(
                message, ToolExecutionContext(cancellation),
            )
        if not isinstance(frame, dict) or "result" not in frame:
            raise ConnectorFailure("unavailable", "MCP response could not be confirmed.")
        result = frame["result"]
        if method == "initialize":
            self.session_id = frame.get("session_id")
            self.protocol_version = result.get("protocolVersion")
        return result


def _response_object(response, request_id):
    if response.headers.get_content_type() == "application/json":
        body = response.read(MAX_BODY + 1)
        if len(body) <= MAX_BODY:
            return json.loads(body)
    elif response.headers.get_content_type() == "text/event-stream":
        left = MAX_BODY
        data = []
        while left > 0:
            line = response.readline(left + 1)
            left -= len(line)
            if not line or left < 0:
                break
            line = line.rstrip(b"\r\n")
            if line.startswith(b"data:"):
                data.append(line[5:].lstrip(b" "))
            elif not line and data:
                event = json.loads(b"\n".join(data))
                data.clear()
                if isinstance(event, dict) and event.get("id") == request_id:
                    return event
    raise ValueError("MCP reply missing or outside size limit.")


def call_mcp_post(message, configuration):
    endpoint = configuration["endpoint"]
    validate_endpoint(endpoint)
    opener = request.build_opener(request.ProxyHandler({}), PinnedHTTPSHandler(False), _NoSearchRedirect())
    req = request.Request(endpoint, data=json.dumps(message).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        **configuration["headers"],
    })
    with opener.open(req, timeout=configuration["timeout"]) as response:
        if "id" not in message:
            if response.status != 202:
                raise ValueError("MCP notification was not accepted.")
            return {"result": {}}
        envelope = _response_object(response, message["id"])
        if (not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0"
                or envelope.get("id") != message["id"] or "error" in envelope
                or not isinstance(envelope.get("result"), dict)):
            raise ValueError("MCP response does not confirm this request.")
        frame = {"result": envelope["result"]}
        if message["method"] == "initialize":
            session = response.headers.get("MCP-Session-Id")
            if session is not None:
                if not session or len(session) > 1024 or any(not 33 <= ord(c) <= 126 for c in session):
                    raise ValueError("Invalid MCP session identifier.")
                frame["session_id"] = session
        return frame
