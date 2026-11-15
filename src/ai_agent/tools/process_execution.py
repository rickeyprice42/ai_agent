"""Run a job process with cooperative cancellation and process-group teardown."""
import os
import signal
import subprocess
from time import monotonic

from ai_agent.tools.base import CURRENT_EXECUTION


def run_process(arguments, *, cwd, timeout, env, input=None):
    context = CURRENT_EXECUTION.get()
    if context is not None and context.cancelled:
        return subprocess.CompletedProcess(arguments, -1, '', 'Выполнение отменено.')
    with subprocess.Popen(arguments, cwd=cwd, stdin=subprocess.PIPE if input is not None else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace', env=env,
                          start_new_session=True) as process:
        deadline = monotonic() + timeout
        pending_input=input
        try:
            while True:
                cancelled = context is not None and context.cancelled
                remaining = deadline - monotonic()
                if cancelled or remaining <= 0:
                    _kill_group(process)
                    stdout, stderr = process.communicate()
                    if cancelled:
                        return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
                    raise subprocess.TimeoutExpired(arguments, timeout, output=stdout, stderr=stderr)
                try:
                    stdout, stderr = process.communicate(input=pending_input,timeout=min(.05, remaining))
                    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    pending_input=None
                    continue
        finally:
            # A reaped PID can be reused by the OS. Never signal it again.
            # The isolated PID namespace destroys surviving descendants when
            # its init exits, including children that created another session.
            if process.poll() is None:
                _kill_group(process)
                process.communicate()


def _kill_group(process):
    try:
        if os.name=='nt':
            # Generated tools on Windows use the AST-restricted runner, which
            # cannot spawn descendants. Code/shell execution stays fail-closed.
            process.kill()
        else:os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
