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


def finish(arguments, configuration):
    import os, socket, subprocess, sys, time
    from pathlib import Path
    from ai_agent.tools import research
    from ai_agent.tools.hosted_research import call_research_browser_result
    if configuration.get('stall'):
        def stall(**kwargs):
            code = "import os,time; from pathlib import Path; os.setsid(); Path('/workspace/ready').write_text('ready'); time.sleep(3); Path('/workspace/late').write_text('late')"
            subprocess.Popen([sys.executable, '-I', '-c', code])
            while True: time.sleep(.02)
        research._research_result = stall
    boundary = {'secret': os.getenv('AVELIN_TEST_SECRET'),
                'database': Path('/var/lib/avelin/avelin.sqlite3').exists(),
                'provider_keys': Path('/etc/avelin/provider-keys.txt').exists()}
    try:
        with socket.create_connection(('1.1.1.1', 80), timeout=.3): boundary['direct_network'] = True
    except OSError: boundary['direct_network'] = False
    result = call_research_browser_result(arguments, configuration)
    result['boundary'] = boundary
    return result
