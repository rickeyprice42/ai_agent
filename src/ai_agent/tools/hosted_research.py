"""Fixed public HTTP and parsing phase; browser and cache stay parent-owned."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from ai_agent.tools.base import ToolResult
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.http import HttpSandbox
from ai_agent.tools.process_host import ProcessHandlerSpec
from ai_agent.tools.research import BrowserResearchReader, ResearchFailure


class HostedResearchHTTPStage:
    def __init__(self, reader, *, timeout_seconds):
        self.reader, self.timeout_seconds = reader, timeout_seconds

    def __call__(self, url, *, locale, region, catalog, adapter, force_browser):
        if force_browser:
            return {'http_text_chars': 0, 'offer_failure': None, 'transport_failure': None}
        http = self.reader.http
        if type(http) is not HttpSandbox or http.allow_private_networks:
            raise ResearchFailure('unsupported_research_profile',
                'Изолированный reader поддерживает только стандартный публичный HTTP.')
        configuration = {'socket_timeout': min(http.timeout_seconds, self.timeout_seconds),
                         'max_response_chars': http.max_response_chars,
                         'max_fragments': self.reader.max_fragments}
        with TemporaryDirectory(prefix='avelin-research-') as folder:
            spec = ProcessHandlerSpec('ai_agent.tools.hosted_research:call_research_http',
                                      Path(folder), configuration=configuration)
            state = _HTTPBroker(spec, timeout_seconds=self.timeout_seconds)(
                {'url': url, 'locale': locale, 'region': region, 'catalog': catalog, 'adapter': adapter})
        if isinstance(state, ToolResult):
            error = state.error
            raise ResearchFailure(error.code if error else 'host_failed', state.text(),
                                  False, error.details if error else {})
        if not isinstance(state, dict):
            raise ResearchFailure('host_protocol', 'Reader вернул некорректный результат.')
        if 'failure' in state:
            raise ResearchFailure(**state['failure'])
        if (('result' in state and isinstance(state['result'], dict)) or
                (type(state.get('http_text_chars')) is int and 'offer_failure' in state
                 and 'transport_failure' in state)):
            return state
        raise ResearchFailure('host_protocol', 'Reader вернул некорректный результат.')


class _DeferredBrowser:
    def availability(self):
        # The worker never starts a browser. Offer verification may require
        # the parent browser phase, which checks actual availability there.
        return True, None


def call_research_http(arguments, configuration):
    reader = BrowserResearchReader(
        HttpSandbox(timeout_seconds=configuration['socket_timeout'],
                    max_response_chars=configuration['max_response_chars']),
        _DeferredBrowser(), max_fragments=configuration['max_fragments'])
    try:
        return reader._read_http_stage(arguments['url'], locale=arguments['locale'],
            region=arguments['region'], catalog=arguments['catalog'],
            adapter=arguments['adapter'], force_browser=False)
    except ResearchFailure as exc:
        return {'failure': {'code': exc.code, 'message': exc.message,
                            'retryable': exc.retryable, 'details': exc.details}}


def enable_research_http_process_host(registry, reader):
    if sys.platform != 'linux':
        return
    tool = registry.get('research_read')
    reader.http_stage = HostedResearchHTTPStage(reader, timeout_seconds=tool.timeout_seconds)
