"""Fixed, reconstructable search broker; never inherits a client or workspace."""
import logging
from dataclasses import replace
import math
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from ai_agent.tools.base import ToolError,ToolResult
from ai_agent.tools.hosted_http import _HTTPBroker
from ai_agent.tools.hosted_search import HostedSearch,validate_search_endpoint
from ai_agent.tools.process_host import ProcessHandlerSpec


class HostedWebSearchHandler:
    requires_workspace_write=False

    def __init__(self,http,*,timeout_seconds):
        self.http,self.timeout_seconds=http,timeout_seconds

    @property
    def may_incur_usage(self):return self.http.search_fallback is not None

    def __call__(self,arguments,context=None):
        if self.http.allow_private_networks:
            return _failure('private_network_disabled','Поисковый процесс разрешает только публичную сеть.')
        fallback=self.http.search_fallback
        if fallback is not None and type(fallback) is not HostedSearch:
            return _failure('unsupported_search_profile','Профиль поискового callback не поддерживает изолированное восстановление.')
        configuration={'socket_timeout':min(self.http.timeout_seconds,self.timeout_seconds),
            'max_response_chars':self.http.max_response_chars,'cooldowns':dict(self.http._search_cooldowns)}
        if fallback is not None:
            try:validate_search_endpoint(fallback.url)
            except ValueError:return _failure('invalid_search_endpoint','Резервный поиск разрешён только через официальный endpoint.')
            # The fixed trusted provider transport receives ONLY its required
            # credential over private stdin, never via environment/files/argv.
            configuration['fallback']={'api_key':fallback.api_key,'model':fallback.model,
                'base_url':fallback.url.removesuffix('/responses'),'timeout':min(fallback.timeout,self.timeout_seconds)}
        with TemporaryDirectory(prefix='avelin-search-') as folder:
            spec=ProcessHandlerSpec('ai_agent.tools.hosted_web_search:call_web_search',Path(folder),configuration=configuration)
            frame=_HTTPBroker(spec,timeout_seconds=self.timeout_seconds)(arguments,context)
        if isinstance(frame,ToolResult):
            if fallback is not None and frame.error and frame.error.code not in {'host_unavailable','host_input_limit'}:
                return replace(frame,error=replace(frame.error,retryable=False,
                    details={**frame.error.details,'outcome_unknown':True,'billable_usage_unknown':True}))
            return frame
        if not isinstance(frame,dict) or not isinstance(frame.get('cooldowns'),dict):
            result=_failure('host_protocol','Поисковый процесс вернул некорректный результат.')
            if fallback is not None:
                result=replace(result,error=replace(result.error,details={'outcome_unknown':True,'billable_usage_unknown':True}))
            return result
        for name,expiry in frame['cooldowns'].items():
            if name in {'duckduckgo_lite','bing_rss'} and type(expiry) in {int,float} and math.isfinite(expiry):
                self.http._search_cooldowns[name]=max(self.http._search_cooldowns.get(name,0),min(expiry,monotonic()+300))
        tokens=frame.get('fallback_tokens')
        tokens=tokens if type(tokens) is int and tokens>=0 else None
        if tokens is not None:
            logging.getLogger('avelin.research').info('hosted search usage=%s',tokens)
        if isinstance(frame.get('error'),str):
            result=_failure('search_failed',frame['error'])
            if frame.get('fallback_attempted') and tokens is None:
                result=replace(result,error=replace(result.error,details={'outcome_unknown':True,'billable_usage_unknown':True}))
            return result
        if not isinstance(frame.get('text'),str):
            result=_failure('host_protocol','Поисковый процесс не вернул текст.')
            if fallback is not None:
                result=replace(result,error=replace(result.error,details={'outcome_unknown':True,'billable_usage_unknown':True}))
            return result
        return frame['text']


def call_web_search(arguments,configuration):
    from ai_agent.tools.http import HttpSandbox
    http=HttpSandbox(timeout_seconds=configuration['socket_timeout'],
        max_response_chars=configuration['max_response_chars'],allow_private_networks=False)
    http._search_cooldowns=dict(configuration.get('cooldowns',{}))
    fallback=configuration.get('fallback')
    if fallback:http.search_fallback=HostedSearch(**fallback)
    frame={}
    try:
        frame['text']=http.search(str(arguments.get('query','')),int(arguments.get('limit',5)),
            str(arguments.get('country','')).strip() or None)
    except ValueError as exc:
        message=str(exc)
        if fallback and fallback['api_key']:message=message.replace(fallback['api_key'],'[redacted]')
        frame['error']=message[:2000]
    frame['cooldowns']=http._search_cooldowns
    usage=getattr(http.search_fallback,'last_usage',None)
    frame['fallback_tokens']=usage.get('total_tokens') if isinstance(usage,dict) else None
    frame['fallback_attempted']=bool(getattr(http.search_fallback,'attempted',False))
    return frame


def enable_web_search_process_host(registry,http):
    if sys.platform!='linux':return
    tool=registry.get('web_search')
    tool.handler=HostedWebSearchHandler(http,timeout_seconds=tool.timeout_seconds)


def _failure(code,message):
    return ToolResult('failed',error=ToolError(code,message),display_text=message)
