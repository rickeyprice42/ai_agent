"""Terminal cancellation must follow handler/process teardown, never precede it."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
import os
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_agent.tools.base import CURRENT_EXECUTION, Tool, ToolRegistry, RetryPolicy
from ai_agent.tools.process_execution import run_process
from ai_agent.tools.process_isolation import isolated_command


class ToolCancellationTests(unittest.TestCase):
    def test_timeout_drains_mutation_without_retrying_or_cancelling_parent(self):
        writes = []
        parent = Event()
        def handler(args):
            time.sleep(.12)
            writes.append('committed before terminal result')
        registry = ToolRegistry()
        registry.register(Tool('mutation', 'Synthetic mutation', {'type': 'object'}, handler,
                               timeout_seconds=.02, retry_policy=RetryPolicy(max_attempts=3)))
        result = registry.invoke('mutation', {}, cancellation=parent)
        self.assertEqual(result.error.code, 'timeout')
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(writes), 1)
        self.assertFalse(parent.is_set())
        time.sleep(.15)
        self.assertEqual(len(writes), 1)
        self.assertIsNone(CURRENT_EXECUTION.get())

    def test_cancel_reaches_handler_before_terminal_result(self):
        started, stopped, parent = Event(), Event(), Event()
        def handler(args, context):
            self.assertIs(CURRENT_EXECUTION.get(), context)
            started.set()
            self.assertTrue(context.cancellation.wait(2))
            stopped.set()
        registry = ToolRegistry()
        registry.register(Tool('cooperative', 'Synthetic fixture', {'type': 'object'}, handler))
        timer = Timer(.1, parent.set)
        timer.start()
        try:
            result = registry.invoke('cooperative', {}, cancellation=parent)
        finally:
            timer.cancel()
        self.assertTrue(started.is_set())
        self.assertTrue(stopped.is_set())
        self.assertEqual(result.status, 'cancelled')

    @unittest.skipUnless(sys.platform == 'linux' and Path('/usr/bin/bwrap').exists(), 'requires Linux bubblewrap')
    def test_cancel_and_timeout_kill_detached_descendant_before_its_write(self):
        for mode in ('cancel', 'timeout'):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                workspace = Path(directory)
                # New session deliberately escapes the original process group.
                child = "import time; from pathlib import Path; time.sleep(.8); Path('late.txt').write_text('BAD')"
                code = ("import subprocess,sys,time; from pathlib import Path; "
                        f"subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); "
                        "Path('ready.txt').write_text('ready'); time.sleep(20)")
                command = isolated_command(workspace, ['python', '-c', code])
                parent = Event()
                def handler(args):
                    return str(run_process(command, cwd=workspace, timeout=10, env={}).returncode)
                registry = ToolRegistry()
                registry.register(Tool('process', 'Synthetic process', {'type': 'object'}, handler,
                                       timeout_seconds=.3 if mode == 'timeout' else 5))
                timer = Timer(.3, parent.set)
                if mode == 'cancel':
                    timer.start()
                started = time.monotonic()
                try:
                    result = registry.invoke('process', {}, cancellation=parent)
                finally:
                    timer.cancel()
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue((workspace / 'ready.txt').exists(), result)
                self.assertEqual(result.status, 'cancelled' if mode == 'cancel' else 'failed')
                time.sleep(1)
                self.assertFalse((workspace / 'late.txt').exists())
