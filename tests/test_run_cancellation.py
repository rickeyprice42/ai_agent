"""Durable ownership and real subprocess cancellation fixtures; no API calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer
import os
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_agent.database import AvelinDatabase, DEFAULT_USER_ID
from ai_agent.tasks import TaskManager
from ai_agent.run_cancellation import RunCancellation
from ai_agent.tools.base import Tool, ToolRegistry
from ai_agent.tools.process_execution import run_process
from ai_agent.tool_factory import _run_sandboxed


class RunCancellationTests(unittest.TestCase):
    def test_pending_stop_keeps_heartbeat_until_drain_and_rejects_wrong_owner(self):
        with TemporaryDirectory() as directory:
            db = AvelinDatabase(Path(directory) / 'db.sqlite')
            manager = TaskManager(db, DEFAULT_USER_ID)
            task = manager.create_task('Read fixture', steps=['Read fixture'])
            manager.ensure_task_running(task.id)
            run = manager.acquire_durable_run('owner', 5)
            self.assertIsNotNone(run)
            guard = RunCancellation(db, DEFAULT_USER_ID, run['id'], 'owner', lease_seconds=5)
            db.request_task_stop(DEFAULT_USER_ID, run['id'], 'cancelled')
            with db.connect() as connection:
                connection.execute("UPDATE task_runs SET heartbeat_at='2000-01-01',lease_expires_at='2000-01-01' WHERE id=?", (run['id'],))
            guard.next_heartbeat = 0
            guard._poll()
            self.assertTrue(guard.is_set())
            current = db.get_task_run(DEFAULT_USER_ID, run['id'])
            self.assertNotEqual(current['heartbeat_at'], '2000-01-01')
            self.assertEqual(current['state'], 'running')
            self.assertIsNone(db.set_task_run_state(DEFAULT_USER_ID, run['id'], 'completed', worker_id='intruder'))
            final = db.set_task_run_state(DEFAULT_USER_ID, run['id'], 'completed', worker_id='owner')
            self.assertEqual(final['state'], 'cancelled')
            self.assertEqual(manager.get_task(task.id).status, 'cancelled')

    def test_generated_runner_receives_unicode_input(self):
        result = _run_sandboxed('def run(arguments):\n    return arguments["text"]\n', {'text':'Привет, Avelin'})
        self.assertEqual(result.status, 'completed', result)
        self.assertEqual(result.output, 'Привет, Avelin')

    def test_durable_deadline_stops_process_before_delayed_write(self):
        with TemporaryDirectory() as directory:
            db = AvelinDatabase(Path(directory) / 'db.sqlite')
            manager = TaskManager(db, DEFAULT_USER_ID)
            task = manager.create_task('Deadline fixture', steps=['Run process'])
            manager.ensure_task_running(task.id)
            run = manager.acquire_durable_run('owner')
            registry = ToolRegistry()
            registry.register(Tool('process', 'Fixture', {'type':'object'},
                lambda args: run_process([sys.executable, '-c',
                    'import time; from pathlib import Path; time.sleep(1); Path("late").write_text("BAD")'],
                    cwd=directory, env=os.environ.copy(), timeout=5)))
            with RunCancellation(db, DEFAULT_USER_ID, run['id'], 'owner', remaining_seconds=.2) as guard:
                result = registry.invoke('process', {}, cancellation=guard)
            self.assertEqual(guard.outcome, 'failed')
            self.assertEqual(result.status, 'cancelled')
            time.sleep(1)
            self.assertFalse((Path(directory) / 'late').exists())

    def test_subprocess_runner_cancels_before_its_five_second_deadline(self):
        registry = ToolRegistry()
        registry.register(Tool('generated', 'Fixture', {'type':'object'},
            lambda args: run_process([sys.executable, '-c', 'import time; time.sleep(20)'],
                                     cwd=Path.cwd(), env=os.environ.copy(), timeout=5),
            timeout_seconds=6))
        parent = Event()
        timer = Timer(.3, parent.set)
        started = time.monotonic()
        timer.start()
        try:result = registry.invoke('generated', {}, cancellation=parent)
        finally:timer.cancel()
        self.assertEqual(result.status, 'cancelled')
        self.assertLess(time.monotonic() - started, 3)

    def test_large_stdin_survives_polling_without_resending_it(self):
        with TemporaryDirectory() as directory:
            data = 'пример' * 20000
            result = run_process([sys.executable, '-c',
                'import sys,time; time.sleep(.15); print(len(sys.stdin.buffer.read().decode("utf-8")))'],
                cwd=directory, env=os.environ.copy(), timeout=3, input=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(int(result.stdout), len(data))


if __name__ == '__main__':unittest.main()
