"""Fake search transports inside a real fresh process; zero provider requests."""
from io import BytesIO
import json


def run(arguments,configuration):
    from ai_agent.tools.http import HttpSandbox
    from ai_agent.tools import hosted_search
    from ai_agent.tools.hosted_web_search import call_web_search
    opens=[]
    def primary(self,req):
        opens.append(req.full_url)
        if configuration.get('fixture')=='fallback' or 'duckduckgo' in req.full_url:
            raw=b'<form id="challenge-form">Verify you are human</form>'
        else:
            raw=b'<rss><channel><item><title>Qwen tool calling report</title><link>https://example.org/qwen</link><description>Qwen tool calling benchmarks.</description></item></channel></rss>'
        response=BytesIO(raw);response.headers={'Content-Type':'text/html'}
        return response
    def official(req,timeout):
        assert req.get_header('Authorization')=='Bearer synthetic-search-key'
        payload={'status':'completed','usage':{'total_tokens':321},'output':[
            {'type':'web_search_call','status':'completed','action':{'sources':[
                {'url':'https://example.org/qwen','title':'Qwen tool calling report'}]}},
            {'type':'message','content':[{'type':'output_text','text':'Qwen tool calling benchmarks.',
                'annotations':[{'type':'url_citation','url':'https://example.org/qwen','title':'Qwen tool calling report'}]}]}]}
        return BytesIO(json.dumps(payload).encode())
    HttpSandbox._open_public=primary
    hosted_search._open_official=official
    result=call_web_search(arguments,configuration)
    result['fixture_opens']=opens
    return result
