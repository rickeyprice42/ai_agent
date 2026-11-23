"""Trusted test-only broker fixture; never registered as a product tool."""
def run(arguments,configuration):
    import os,socket,subprocess,sys,time
    from pathlib import Path
    operation=configuration['operation']
    if operation == 'mcp':
        import io, json, time
        from email.message import Message
        from types import SimpleNamespace
        from urllib import request
        from ai_agent.tools.hosted_mcp import call_mcp_post

        class Response(io.BytesIO):
            status = 200

        def open_request(req, timeout):
            rpc = json.loads(req.data)
            if rpc["method"] == "tools/call":
                assert req.get_header("Mcp-session-id") == "fixture-session"
                if rpc["params"]["arguments"].get("query") == "hang":
                    while True:
                        time.sleep(.1)
            result = {"protocolVersion": "2025-11-25", "tools": [{
                "name": "lookup", "inputSchema": {"type": "object", "properties": {
                    "query": {"type": "string"}}}, "annotations": {"readOnlyHint": True},
            }]}
            envelope = json.dumps({"jsonrpc": "2.0", "id": rpc.get("id"), "result": result}).encode()
            sse = rpc["method"] == "tools/call"
            response = Response(b"data: " + envelope + b"\n\n" if sse else envelope)
            response.status = 202 if "id" not in rpc else 200
            response.headers = Message()
            response.headers["Content-Type"] = "text/event-stream" if sse else "application/json"
            response.headers["MCP-Session-Id"] = "fixture-session"
            return response

        request.build_opener = lambda *args: SimpleNamespace(open=open_request)
        return call_mcp_post(arguments, configuration)
    if operation == 'vision':
        from ai_agent.llm import vision
        from ai_agent.tools.hosted_vision import call_vision_request

        def fake_send(url, payload, timeout, headers, *, provider, opener):
            assert payload["image"] == "a" * 2_100_000
            assert headers["Authorization"] == "Bearer synthetic"
            assert not Path("/var/lib/avelin/avelin.sqlite3").exists()
            return {"output_text": "Synthetic image description"}

        vision._send_json_request = fake_send
        return call_vision_request(arguments, configuration)
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
