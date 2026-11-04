from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import launch_local

CORE_FRONTEND = (200, '<html><head><meta name="avelin-app" content="core-v2"></head></html>')
REAL_TERMINATE_TREE = launch_local.terminate_owned_process_tree

def current_health(instance='fixture', pid=987654, launcher_id='fixture-launch'):
    return (200, json.dumps({'status': 'ok', 'restart_required': False, 'runtime': {
        'contract': launch_local.API_CONTRACT, 'source_signature': launch_local.source_signature(),
        'installation_id': launch_local.installation_id(), 'instance_id': instance, 'process_id': pid,
        'launcher_id': launcher_id}}))


class LauncherRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        def fake_tree(process):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            return True
        for patcher in [patch.object(launch_local, 'LOGS', directory),
                        patch.object(launch_local, 'RUNTIME_STATE', directory / 'runtime.json'),
                        patch.object(launch_local, 'uuid4', return_value=SimpleNamespace(hex='fixture-launch')),
                        patch.object(launch_local, 'terminate_owned_process_tree', side_effect=fake_tree)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_launcher_recovers_missing_frontend_when_backend_is_already_ready(self) -> None:
        process = Mock()
        process.poll.return_value = None

        with TemporaryDirectory() as temp_dir:
            with patch.object(launch_local, "LOGS", Path(temp_dir)), patch.object(
                launch_local.AvelinLauncher, "validate"
            ), patch.object(
                launch_local, "fetch", side_effect=[current_health(), None]
            ), patch.object(launch_local, "wait_for", return_value=True), patch.object(
                launch_local.subprocess, "Popen", return_value=process
            ) as popen:
                launcher = launch_local.AvelinLauncher()
                message = launcher.start()
                launcher.stop()

        self.assertIn("frontend", message)
        self.assertEqual(popen.call_count, 1)
        command = popen.call_args.args[0]
        self.assertIn("vite.js", " ".join(command))
        process.terminate.assert_called_once()

    def test_launcher_reports_foreign_backend_port_without_starting_processes(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(launch_local, "LOGS", Path(temp_dir)), patch.object(
            launch_local.AvelinLauncher, "validate"
        ), patch.object(launch_local, "fetch", side_effect=[(200, "not avelin"), None]), patch.object(
            launch_local.subprocess, "Popen"
        ) as popen:
            launcher = launch_local.AvelinLauncher()
            with self.assertRaisesRegex(RuntimeError, "8000"):
                launcher.start()

        popen.assert_not_called()

    def test_legacy_health_is_not_reused_as_a_current_server(self):
        with TemporaryDirectory() as temp_dir, patch.object(launch_local, 'LOGS', Path(temp_dir)), patch.object(
            launch_local.AvelinLauncher, 'validate'), patch.object(launch_local, 'fetch', side_effect=[(200, '{"status":"ok"}'), CORE_FRONTEND]), patch.object(
            launch_local.subprocess, 'Popen') as popen, patch.object(launch_local.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'устаревший'):
                launch_local.AvelinLauncher().start()
        popen.assert_not_called()
        kill.assert_not_called()

    def test_signature_and_contract_detect_actual_loaded_code_instead_of_port_health(self):
        health = current_health()
        self.assertTrue(launch_local.backend_is_current(health))
        for key, value in [('source_signature', 'old-source'), ('contract', 1), ('installation_id', 'foreign')]:
            payload = json.loads(health[1])
            payload['runtime'][key] = value
            with self.subTest(key=key):
                self.assertFalse(launch_local.backend_is_current((200, json.dumps(payload))))
        payload = json.loads(health[1])
        payload['restart_required'] = True
        self.assertFalse(launch_local.backend_is_current((200, json.dumps(payload))))

    def test_managed_stop_requires_exact_saved_instance_and_installation(self):
        with TemporaryDirectory() as directory, patch.object(launch_local, 'RUNTIME_STATE', Path(directory) / 'runtime.json'), patch.object(
            launch_local.os, 'kill') as kill:
            health = current_health()
            runtime = json.loads(health[1])['runtime']
            launch_local.RUNTIME_STATE.write_text(json.dumps({**runtime, 'instance_id': 'previous-instance'}))
            self.assertFalse(launch_local.stop_managed_backend(health))
            kill.assert_not_called()
            launch_local.RUNTIME_STATE.write_text(json.dumps(runtime))
            self.assertTrue(launch_local.stop_managed_backend(health))
            kill.assert_called_once_with(runtime['process_id'], launch_local.signal.SIGTERM)
            self.assertFalse(launch_local.RUNTIME_STATE.exists())

    def test_stale_managed_backend_is_replaced_and_child_pid_is_stopped_on_close(self):
        process = Mock()
        process.poll.return_value = None
        stale = json.loads(current_health('old-instance')[1])
        stale['runtime']['source_signature'] = 'old-source'
        stale = (200, json.dumps(stale))
        fresh = current_health('new-instance')
        with TemporaryDirectory() as directory, patch.object(launch_local, 'LOGS', Path(directory)), patch.object(
            launch_local, 'RUNTIME_STATE', Path(directory) / 'runtime.json'), patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(
            launch_local, 'fetch', side_effect=[stale, CORE_FRONTEND, fresh]), patch.object(
            launch_local, 'wait_for', side_effect=lambda url, predicate, **kwargs: predicate(*fresh)), patch.object(launch_local, 'wait_for_backend_stop', return_value=True), patch.object(
            launch_local.subprocess, 'Popen', return_value=process) as popen, patch.object(launch_local.os, 'kill') as kill:
            launch_local.RUNTIME_STATE.write_text(json.dumps(json.loads(stale[1])['runtime']))
            launcher = launch_local.AvelinLauncher()
            message = launcher.start()
            self.assertIn('backend', message)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(launcher.backend_instance['instance_id'], 'new-instance')
            launcher.stop()
            self.assertEqual(kill.call_count, 2)
            self.assertFalse(launch_local.RUNTIME_STATE.exists())

    def test_frontend_startup_failure_also_stops_the_actual_backend_child(self):
        process = Mock()
        process.poll.return_value = None
        fresh = current_health()
        with TemporaryDirectory() as directory, patch.object(launch_local, 'LOGS', Path(directory)), patch.object(
            launch_local, 'RUNTIME_STATE', Path(directory) / 'runtime.json'), patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(
            launch_local, 'fetch', side_effect=[None, None, fresh]), patch.object(launch_local, 'wait_for', side_effect=lambda url, predicate, **kwargs: predicate(*fresh) if url == launch_local.BACKEND_URL else False), patch.object(
            launch_local.subprocess, 'Popen', return_value=process), patch.object(launch_local.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'не стали готовы'):
                launch_local.AvelinLauncher().start()
            kill.assert_called_once_with(json.loads(fresh[1])['runtime']['process_id'], launch_local.signal.SIGTERM)

    def test_health_source_signature_changes_when_core_source_changes(self):
        from backend.runtime_version import source_signature
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'backend').mkdir()
            source = root / 'backend/routes.py'
            source.write_bytes(b'value = 1\r\n')
            first = source_signature(root)
            source.write_bytes(b'value = 1\n')
            self.assertEqual(source_signature(root), first)
            source.write_bytes(b'value = 2\n')
            self.assertNotEqual(source_signature(root), first)

    def test_frontend_requires_core_marker_not_another_avelin_application(self):
        self.assertTrue(launch_local.frontend_is_current(CORE_FRONTEND))
        self.assertTrue(launch_local.frontend_is_current((200, "<meta content='core-v2' name='avelin-app'>")))
        for response in [(200, '<title>Avelin Health</title>'), (200, '<meta name="avelin-app" content="core-v1">'), (503, CORE_FRONTEND[1])]:
            with self.subTest(response=response):
                self.assertFalse(launch_local.frontend_is_current(response))

    def test_owned_readiness_captures_validated_identity_without_a_second_health_fetch(self):
        process = Mock(pid=112233)
        process.poll.return_value = None
        fresh = current_health('captured-once')
        original_nonce = os.environ.get('AVELIN_LAUNCH_ID')
        with patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(launch_local, 'fetch',
                side_effect=[None, CORE_FRONTEND, None]) as fetch, patch.object(launch_local, 'wait_for',
                side_effect=lambda url, predicate, **kwargs: predicate(*fresh)), patch.object(
                launch_local.subprocess, 'Popen', return_value=process) as popen, patch.object(launch_local.os, 'kill') as kill:
            launcher = launch_local.AvelinLauncher()
            launcher.start()
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(launcher.backend_instance['instance_id'], 'captured-once')
            self.assertEqual(popen.call_args.kwargs['env']['AVELIN_LAUNCH_ID'], 'fixture-launch')
            self.assertEqual(os.environ.get('AVELIN_LAUNCH_ID'), original_nonce)
            launcher.stop()  # Health disappeared; the owned process tree still stops.
            process.terminate.assert_called_once()
            kill.assert_not_called()
            self.assertFalse(launch_local.RUNTIME_STATE.exists())

    def test_racing_winner_is_never_claimed_or_killed_even_when_its_code_is_current(self):
        for spawned_exited in [False, True]:
            with self.subTest(spawned_exited=spawned_exited):
                process = Mock(pid=112233)
                process.poll.return_value = 1 if spawned_exited else None
                winner = current_health('winner-instance', pid=998877, launcher_id='another-launcher')
                with patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(launch_local, 'fetch',
                        side_effect=[None, CORE_FRONTEND]), patch.object(launch_local, 'wait_for',
                        side_effect=lambda url, predicate, **kwargs: predicate(*winner)), patch.object(
                        launch_local.subprocess, 'Popen', return_value=process), patch.object(launch_local.os, 'kill') as kill:
                    launcher = launch_local.AvelinLauncher()
                    with self.assertRaisesRegex(RuntimeError, 'не стали готовы'):
                        launcher.start()
                    self.assertIsNone(launcher.backend_instance)
                    self.assertFalse(launch_local.RUNTIME_STATE.exists())
                    kill.assert_not_called()
                    if spawned_exited:
                        process.terminate.assert_not_called()
                    else:
                        process.terminate.assert_called_once()

    def test_unresponsive_health_during_failed_startup_cleans_both_owned_trees(self):
        backend, frontend = Mock(pid=112233), Mock(pid=112234)
        backend.poll.return_value = frontend.poll.return_value = None
        with patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(launch_local, 'fetch',
                side_effect=[None, None]), patch.object(launch_local, 'wait_for', return_value=False), patch.object(
                launch_local.subprocess, 'Popen', side_effect=[backend, frontend]), patch.object(launch_local.os, 'kill') as kill:
            launcher = launch_local.AvelinLauncher()
            with self.assertRaisesRegex(RuntimeError, 'не стали готовы'):
                launcher.start()
            backend.terminate.assert_called_once()
            frontend.terminate.assert_called_once()
            self.assertEqual(launcher.processes, [])
            kill.assert_not_called()

    def test_stop_interrupts_inflight_start_and_serializes_cleanup(self):
        entered = threading.Event()
        failures = []
        processes = [Mock(pid=112233), Mock(pid=112234)]
        for process in processes:
            process.poll.return_value = None
        def waiting(url, predicate, **kwargs):
            entered.set()
            self.assertTrue(kwargs['cancel'].wait(2))
            return False
        with patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(launch_local, 'fetch',
                return_value=None), patch.object(launch_local, 'wait_for', side_effect=waiting), patch.object(
                launch_local.subprocess, 'Popen', side_effect=processes):
            launcher = launch_local.AvelinLauncher()
            def starting():
                try:
                    launcher.start()
                except RuntimeError as exc:
                    failures.append(str(exc))
            thread = threading.Thread(target=starting)
            thread.start()
            self.assertTrue(entered.wait(2))
            launcher.stop()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(failures and 'отменён' in failures[0])
            self.assertEqual(launcher.processes, [])
            for process in processes:
                process.terminate.assert_called_once()

    def test_close_between_spawns_cancels_frontend_and_cleans_created_backend(self):
        process = Mock(pid=112233)
        process.poll.return_value = None
        launcher = launch_local.AvelinLauncher()
        def spawn(*args, **kwargs):
            launcher.request_stop(permanent=True)
            return process
        with patch.object(launch_local.AvelinLauncher, 'validate'), patch.object(launch_local, 'fetch',
                return_value=None), patch.object(launch_local.subprocess, 'Popen', side_effect=spawn) as popen:
            with self.assertRaisesRegex(RuntimeError, 'отменён'):
                launcher.start()
            self.assertEqual(popen.call_count, 1)
            process.terminate.assert_called_once()
            self.assertEqual(launcher.processes, [])

    def test_permanent_close_before_start_cannot_be_cleared_by_a_late_restart_worker(self):
        launcher = launch_local.AvelinLauncher()
        launcher.request_stop(permanent=True)
        with patch.object(launch_local.subprocess, 'Popen') as popen:
            for action in [launcher.start, launcher.restart]:
                with self.assertRaisesRegex(RuntimeError, 'отменён'):
                    action()
            popen.assert_not_called()

    def test_explicit_restart_can_clear_an_ordinary_stop(self):
        launcher = launch_local.AvelinLauncher()
        launcher.stop()
        with self.assertRaisesRegex(RuntimeError, 'отменён'):
            launcher.start()
        with patch.object(launcher, '_start_locked', return_value='fresh'):
            self.assertEqual(launcher.restart(), 'fresh')

    def test_managed_restart_refuses_missing_or_changed_launcher_nonce(self):
        original = current_health()
        runtime = json.loads(original[1])['runtime']
        launch_local.RUNTIME_STATE.write_text(json.dumps(runtime))
        for nonce in ['', 'another-launcher']:
            changed = current_health(launcher_id=nonce)
            with self.subTest(nonce=nonce), patch.object(launch_local.os, 'kill') as kill:
                self.assertFalse(launch_local.stop_managed_backend(changed))
                kill.assert_not_called()
        self.assertTrue(launch_local.RUNTIME_STATE.exists())

    def test_backend_stop_checks_tcp_listener_not_failed_http_health(self):
        with patch.object(launch_local, 'fetch', return_value=None) as fetch, patch.object(
                launch_local.socket, 'create_connection') as connect, patch.object(
                launch_local.time, 'monotonic', side_effect=[0, .01, .2]), patch.object(launch_local.time, 'sleep'):
            self.assertFalse(launch_local.wait_for_backend_stop(timeout=.1))
            fetch.assert_not_called()
            connect.assert_called_once()
        with patch.object(launch_local.socket, 'create_connection', side_effect=ConnectionRefusedError):
            self.assertTrue(launch_local.wait_for_backend_stop())

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree semantics')
    def test_tree_cleanup_targets_only_the_live_owned_root_and_skips_exited_pid(self):
        process = Mock(pid=112233)
        process.poll.return_value = None
        with patch.object(launch_local.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(REAL_TERMINATE_TREE(process))
            self.assertEqual(run.call_args.args[0], ['taskkill', '/PID', '112233', '/T', '/F'])
        process.poll.return_value = 0
        with patch.object(launch_local.subprocess, 'run') as run:
            self.assertTrue(REAL_TERMINATE_TREE(process))
            run.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree semantics')
    def test_failed_tree_kill_does_not_kill_only_the_venv_stub_and_orphan_children(self):
        process = Mock(pid=112233)
        process.poll.return_value = None
        with patch.object(launch_local.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            self.assertFalse(REAL_TERMINATE_TREE(process))
            process.kill.assert_not_called()
            process.terminate.assert_not_called()

    def test_failed_cleanup_retains_owned_handle_for_retry(self):
        launcher = launch_local.AvelinLauncher()
        process = Mock(pid=112233)
        launcher.processes.append(process)
        with patch.object(launch_local, 'terminate_owned_process_tree', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Не удалось остановить'):
                launcher.stop()
            self.assertEqual(launcher.processes, [process])


if __name__ == "__main__":
    unittest.main()
