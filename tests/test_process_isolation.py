from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_agent.tools.process_isolation import isolated_command
from ai_agent.tools.code_tools import BoundedProcessRunner, CodeSandbox
from ai_agent.tools.shell import ShellSandbox


class ProcessIsolationTests(unittest.TestCase):
    def test_missing_isolation_denies_both_execution_entry_points(self):
        with TemporaryDirectory() as folder, patch('ai_agent.tools.process_isolation.sys.platform', 'win32'), patch('subprocess.run') as execute:
            for runner in (lambda: BoundedProcessRunner(Path(folder)).run(['python', '-V']),
                           lambda: ShellSandbox(Path(folder)).run('python -m pytest')):
                with self.assertRaisesRegex(ValueError, 'Изолированный исполнитель недоступен'):
                    runner()
            execute.assert_not_called()

    def test_linux_command_hides_host_state_and_drops_network(self):
        with TemporaryDirectory() as folder, patch('ai_agent.tools.process_isolation.sys.platform', 'linux'), patch('ai_agent.tools.process_isolation.Path.is_file', return_value=True):
            command = isolated_command(Path(folder), ['python', '-m', 'unittest'])
            self.assertIn('--unshare-all', command)
            self.assertIn('--clearenv', command)
            self.assertIn('--cap-drop', command)
            self.assertNotIn('/var/lib/avelin', command)
            self.assertNotIn('/etc/avelin', command)
            self.assertEqual(command[-6:], ['--', sys.executable, '-s', '-E', '-m', 'unittest'])

    @unittest.skipUnless(sys.platform == 'linux' and Path('/usr/bin/bwrap').exists(), 'requires Linux bubblewrap')
    def test_real_test_runner_cannot_read_sibling_or_host_and_can_write_result(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = root / 'alice'
            tests = workspace / 'tests'
            tests.mkdir(parents=True)
            secret = root / 'bob-secret.txt'
            secret.write_text('synthetic private data')
            code = '''import os, socket, unittest
from pathlib import Path
class BoundaryTest(unittest.TestCase):
    def test_boundary(self):
        self.assertFalse(Path(HOST_SECRET).exists())
        self.assertFalse(Path('/etc/avelin/provider-keys.txt').exists())
        self.assertFalse(Path('/var/lib/avelin/avelin.sqlite3').exists())
        self.assertNotIn('AVELIN_TEST_SECRET', os.environ)
        with self.assertRaises(OSError):
            socket.create_connection(('1.1.1.1', 443), timeout=.2)
        Path('result.txt').write_text('normal execution works')
'''.replace('HOST_SECRET', repr(str(secret)))
            (tests / 'test_boundary.py').write_text(code)
            with patch.dict(os.environ, {'AVELIN_TEST_SECRET': 'synthetic'}):
                result = CodeSandbox(workspace).run_check('python_unittest')
            self.assertEqual(result['return_code'], 0, result)
            self.assertEqual((workspace / 'result.txt').read_text(), 'normal execution works')
            self.assertEqual(secret.read_text(), 'synthetic private data')
