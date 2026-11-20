"""Synthetic public pages/parser stalls inside the real research process."""
def run(arguments, configuration):
    import os
    from ai_agent.tools import research
    from ai_agent.tools.http import HttpSandbox, HttpTransportError
    from ai_agent.tools.hosted_research import call_research_http
    mode = configuration.get('fixture', 'article')
    def fetch(self, url, **kwargs):
        if mode == 'tls':
            raise HttpTransportError('tls_error', 'Synthetic TLS failure')
        body = ('<main>' + 'Verified public research evidence. ' * 20 + '</main>')
        if mode == 'dynamic':
            body = '<title>Dynamic</title><div id="root"></div><script>load()</script>'
        if mode == 'antibot':
            body = '<main>Verify you are human captcha</main>'
        return {'url': url, 'method': 'GET', 'status': 403 if mode == 'antibot' else 200,
                'headers': {'Content-Type': 'text/html'}, 'body': body, 'truncated': False}
    HttpSandbox.fetch = fetch
    if mode == 'slow_parser':
        def parse(*args, **kwargs):
            from http_host_fixture import run as stall
            return stall({'port': configuration['port'], 'delay': 2}, {'operation': 'late_network'})
        research.extract_public_page = parse
    assert not os.environ.get('OPENAI_API_KEY')
    return call_research_http(arguments, configuration)
