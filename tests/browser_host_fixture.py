"""Synthetic transports and hostile lifecycle fixtures; never product tools."""
def proxy(arguments, configuration):
    import socket
    from ai_agent.tools import egress_proxy
    from ai_agent.tools.hosted_browser import call_browser_proxy
    original_resolve = socket.getaddrinfo
    original_connect = egress_proxy.connect_public
    def resolve(host, port, *args, **kwargs):
        if host == 'fixture.test':
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port or 80))]
        return original_resolve(host, port, *args, **kwargs)
    def connect(address, **kwargs):
        if address[0] == 'fixture.test':
            return socket.create_connection(('127.0.0.1', configuration['fixture_port']), timeout=3)
        return original_connect(address, **kwargs)
    socket.getaddrinfo = resolve
    egress_proxy.connect_public = connect
    if configuration.get('stall_proxy'):
        from http_host_fixture import run
        return run({'port': configuration['signal_port'], 'delay': configuration['delay']}, {'operation': 'late_network'})
    return call_browser_proxy(arguments, configuration)


def browser(arguments, configuration):
    import os, socket, subprocess, sys, time
    from pathlib import Path
    from ai_agent.tools import hosted_browser
    boundary = {'env_secret': os.getenv('AVELIN_TEST_SECRET'),
                'database': Path('/var/lib/avelin/avelin.sqlite3').exists(),
                'provider_keys': Path('/etc/avelin/provider-keys.txt').exists(),
                'workspace_files': [p.name for p in Path('/workspace').iterdir()]}
    for name, address in [('direct_public', ('1.1.1.1', 80)),
                          ('host_loopback', ('127.0.0.1', configuration['fixture_port']))]:
        try:
            with socket.create_connection(address, timeout=.3): boundary[name] = True
        except OSError: boundary[name] = False
    if configuration.get('stall_browser'):
        def stall(self, page):
            child = '''import os,socket,time,base64
os.setsid()
def send(path):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
  sock.connect('/egress/proxy.sock')
  auth=base64.b64encode((USER+':'+PASSWORD).encode()).decode()
  sock.sendall(('GET http://fixture.test/'+path+' HTTP/1.1\\r\\nHost: fixture.test\\r\\nProxy-Authorization: Basic '+auth+'\\r\\nConnection: close\\r\\n\\r\\n').encode())
  sock.recv(100)
send('child-ready')
time.sleep(DELAY)
send('late')
'''.replace('USER', repr(configuration['proxy_user'])).replace('PASSWORD', repr(configuration['proxy_password'])).replace('DELAY', str(configuration['delay']))
            subprocess.Popen([sys.executable, '-I', '-c', child])
            while True: time.sleep(.05)
        hosted_browser._BrokeredRunner._snapshot = stall
    result = hosted_browser.call_browser_inner(arguments, configuration)
    result['boundary'] = boundary
    return result
