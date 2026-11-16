"""Trusted test-only broker fixture; never registered as a product tool."""
def run(arguments,configuration):
    import os,socket,subprocess,sys,time
    from pathlib import Path
    operation=configuration['operation']
    if operation=='boundary':
        return {'secret_env':os.environ.get('AVELIN_TEST_SECRET'),
            'provider_keys':Path('/etc/avelin/provider-keys.txt').exists(),
            'database':Path('/var/lib/avelin/avelin.sqlite3').exists(),
            'workspace_files':[p.name for p in Path('/workspace').iterdir()]}
    if operation=='rebind':
        from ai_agent.tools.hosted_http import call_public_http
        original=socket.getaddrinfo
        def resolve(host,port,*args,**kwargs):
            if host=='rebind.test':
                return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34' if port is None else '127.0.0.1',port or 0))]
            return original(host,port,*args,**kwargs)
        socket.getaddrinfo=resolve
        return call_public_http({'url':f"http://rebind.test:{arguments['port']}/sentinel"},
            {'socket_timeout':2,'max_response_chars':2000})
    if operation=='late_network':
        port=arguments['port']
        child="""import os,socket,time
os.setsid()
def send(value):
 with socket.create_connection(('127.0.0.1',PORT),timeout=1) as s:s.sendall(value)
send(b'CHILD_READY')
time.sleep(DELAY)
send(b'LATE')
""".replace('PORT',str(port)).replace('DELAY',str(arguments.get('delay',1)))
        subprocess.Popen([sys.executable,'-I','-c',child])
        while True:time.sleep(.02)
    raise ValueError('Unknown fixture')
