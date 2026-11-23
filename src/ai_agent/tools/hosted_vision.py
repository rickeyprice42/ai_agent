"""Fixed process transport for billable vision requests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import request
from urllib.parse import urlsplit

from ai_agent.llm.base import ProviderError
from ai_agent.tools.base import ToolResult
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.hosted_search import _NoSearchRedirect
from ai_agent.tools.process_host import ProcessHandlerSpec
from ai_agent.tools.public_transport import PinnedHTTPSHandler


def _check_endpoint(url):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.port in (None, 443)
                 and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                 and (parsed.hostname, parsed.path) in {
                     ("api.openai.com", "/v1/responses"), ("ollama.com", "/api/chat"),
                 })
    except ValueError:
        valid = False
    if not valid:
        raise ProviderError("Для анализа в изолированном процессе нужен официальный HTTPS endpoint.", "unavailable_profile")


def hosted_vision_request(url, payload, timeout_seconds, headers, *, provider):
    _check_endpoint(url)
    with TemporaryDirectory(prefix="avelin-vision-") as folder:
        workspace = Path(folder)
        # A normalized photo can exceed the JSON IPC limit. Mount just this
        # request body read-only and keep credentials in the private IPC input.
        (workspace / "request.json").write_text(json.dumps(payload), encoding="utf-8")
        spec = ProcessHandlerSpec(
            "ai_agent.tools.hosted_vision:call_vision_request", workspace,
            configuration={"url": url, "timeout": timeout_seconds, "headers": headers, "provider": provider},
        )
        frame = _HTTPBroker(spec, timeout_seconds=timeout_seconds)({})
    if isinstance(frame, ToolResult):
        code = frame.error.code if frame.error else "host_failed"
        if code in {"host_unavailable", "host_input_limit"}:
            code = "unavailable_profile"
        elif code == "cancelled":
            code = "request_interrupted"
        raise ProviderError(frame.text(), code)
    if isinstance(frame, dict) and isinstance(frame.get("response"), dict):
        return frame["response"]
    if isinstance(frame, dict) and "error" in frame:
        raise ProviderError(frame["message"], frame["error"])
    raise ProviderError("Не удалось подтвердить результат анализа изображения.", "host_protocol")


def call_vision_request(arguments, configuration):
    from ai_agent.llm.vision import _send_json_request

    url = configuration["url"]
    _check_endpoint(url)
    opener = request.build_opener(request.ProxyHandler({}), PinnedHTTPSHandler(False), _NoSearchRedirect())
    payload = json.loads(Path("/workspace/request.json").read_text(encoding="utf-8"))
    try:
        return {"response": _send_json_request(url, payload, configuration["timeout"],
            configuration["headers"], provider=configuration["provider"], opener=opener.open)}
    except ProviderError as exc:
        return {"error": exc.code, "message": exc.user_message}
