"""Private trusted-code entry point; launched with Python -I inside bubblewrap."""
import importlib
import json
import math
import os
from pathlib import Path
import resource
import sys
from dataclasses import asdict

MAX_BYTES = 2 * 1024 * 1024


def main(*, address_space_limit=1024**3):
    # Code roots are mounted read-only by the parent. Neither CWD nor the
    # writable workspace is added to the import path.
    sys.path[:0] = ['/avelin_code/0'] + (['/avelin_code/1'] if Path('/avelin_code/1').exists() else [])
    from ai_agent.tools.base import ToolResult, ToolError
    raw = sys.stdin.buffer.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('IPC input limit')
    payload = json.loads(raw)
    if address_space_limit is not None:
        resource.setrlimit(resource.RLIMIT_AS,(address_space_limit,address_space_limit))
    seconds = max(1,math.ceil(float(payload['timeout_seconds']))+1)
    resource.setrlimit(resource.RLIMIT_CPU,(seconds,seconds))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    # The only protocol FD is non-inheritable. Ordinary stdout/stderr, including
    # child process output, cannot flood/corrupt the bounded JSON channel.
    wire = os.dup(sys.stdout.fileno())
    os.set_inheritable(wire,False)
    with open(os.devnull,'wb') as null:
        os.dup2(null.fileno(),1)
        os.dup2(null.fileno(),2)
    try:
        module,function = payload['entrypoint'].split(':',1)
        handler = getattr(importlib.import_module(module),function)
        result = handler(payload['arguments'],payload['configuration'])
    except ValueError as exc:
        message = str(exc) if payload.get('expose_value_errors') else 'Обработчик отклонил операцию.'
        result = ToolResult('failed',error=ToolError('invalid_operation',message),display_text=message)
    except Exception:
        result = ToolResult('failed',error=ToolError('internal_error','Обработчик завершился внутренней ошибкой.'))
    frame = {'kind':'result','value':asdict(result)} if isinstance(result,ToolResult) else {'kind':'raw','value':result}
    try:
        encoded = json.dumps(frame,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(encoded)>MAX_BYTES:raise ValueError('IPC output limit')
    except (ValueError,TypeError):
        encoded = json.dumps({'kind':'result','value':{'status':'failed','error':{'code':'invalid_output','message':'Результат обработчика не помещается в JSON-контракт.'}}}).encode()
    while encoded:
        written = os.write(wire,encoded)
        encoded = encoded[written:]
    os.close(wire)


if __name__=='__main__':main()
