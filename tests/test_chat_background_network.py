"""Acceptance checks using a real socket, not TestClient's buffered ASGI transport."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ChatBackgroundNetworkTests(unittest.TestCase):
    def run_scenario(self,scenario):
        with TemporaryDirectory() as directory:
            output=Path(directory)
            process=subprocess.run([sys.executable,str(ROOT/'scripts/verify_chat_background.py'),
                '--mode','durable','--scenario',scenario,'--output',str(output)],cwd=ROOT,
                capture_output=True,text=True,encoding='utf-8',timeout=38)
            report_path=output/'result.json'
            self.assertTrue(report_path.is_file(),'Isolated HTTP worker did not produce a report: '+process.stderr[-1000:])
            report=json.loads(report_path.read_text('utf-8'))
            self.assertEqual(process.returncode,0,report.get('failure') or process.stderr[-1000:])
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(report['external_model_calls'],0)
            self.assertEqual(report['external_push_calls'],0)
            self.assertTrue(any(event['event']=='socket_disconnected' for event in report['timeline']))
            self.assertEqual(report['provider_calls'],1)
            return report

    def test_run_survives_http_socket_disconnect_reload_and_replay(self):
        report=self.run_scenario('disconnect')
        self.assertEqual(report['message_status'],'complete')
        self.assertTrue(report['message_contains_final'])
        self.assertEqual(report['notification_count'],1)
        self.assertGreater(report['replayed_events'],0)

    def test_explicit_stop_after_disconnect_cancels_without_completion_notice(self):
        report=self.run_scenario('cancel')
        self.assertEqual(report['snapshot']['state'],'cancelled')
        self.assertEqual(report['notification_count'],0)


if __name__=='__main__':unittest.main()
