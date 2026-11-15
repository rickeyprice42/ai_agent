"""Trusted synthetic module mounted read-only only by process-host tests."""
from pathlib import Path
import os
import socket
import subprocess
import sys
import time


def run(arguments, configuration):
    operation = configuration.get('operation','echo')
    if operation=='echo':return arguments
    if operation=='probe':
        try:Path(arguments['host_path']).read_text();outside=True
        except OSError:outside=False
        try:socket.create_connection(('1.1.1.1',443),timeout=.1).close();network=True
        except OSError:network=False
        return {'outside':outside,'network':network,'secret':os.getenv('AVELIN_HOST_TEST_SECRET'),
                'cwd_in_path':os.getcwd() in sys.path}
    if operation=='noisy':
        os.write(1,b'noise'*100000)
        print('incidental output')
        return {'ok':True}
    if operation=='secret_error':raise ValueError('sk-fixture-must-not-leak')
    if operation=='invalid_output':return {object()}
    if operation=='typed':
        from ai_agent.tools.base import ToolResult
        return ToolResult('completed',output={'verified':'structured'})
    if operation=='typed_bad':
        from ai_agent.tools.base import ToolResult
        return ToolResult('completed',output={},display_text={'bad':'type'})
    if operation=='memory':
        data = bytearray(2*1024**3)
        return {'size':len(data)}
    if operation=='atomic':
        from ai_agent.tools.atomic_files import atomic_output
        with atomic_output(Path('/workspace/original.txt'),overwrite=True) as temporary:
            temporary.write_text('PARTIAL')
            Path('/workspace/ready.txt').write_text('ready')
            time.sleep(5)
            temporary.write_text('FINAL')
        return {'published':True}
    if operation in {'detached','detached_return'}:
        child = "import time; from pathlib import Path; time.sleep(.8); Path('late.txt').write_text('BAD')"
        subprocess.Popen([sys.executable,'-I','-c',child],start_new_session=True)
    Path('/workspace/ready.txt').write_text('ready')
    if operation=='detached_return':return {'started':True}
    if operation=='mutation':
        path = Path('/workspace/count.txt')
        path.write_text(str(int(path.read_text())+1) if path.exists() else '1')
    # Deliberately ignore every cancellation signal and never return.
    time.sleep(5)
    Path('/workspace/late.txt').write_text('BAD')
    return {'late_effect':True}
