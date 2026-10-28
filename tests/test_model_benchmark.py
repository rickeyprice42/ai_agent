from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import importlib.util
import json
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "benchmark_models", ROOT / "scripts" / "benchmark_models.py"
)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class FakeOllamaHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_GET(self):
        if self.path != "/api/tags":
            self.send_error(404)
            return
        self._reply({"models": [{"name": "test-model:latest"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.requests.append({"path": self.path, "payload": payload})
        if self.path == "/api/show":
            self._reply(
                {
                    "details": {
                        "family": "test",
                        "parameter_size": "1B",
                        "quantization_level": "Q4_K_M",
                    },
                    "model_info": {"test.context_length": 8192},
                    "license": "MIT",
                }
            )
            return
        if self.path == "/api/chat":
            self._reply(
                {
                    "message": {"role": "assistant", "content": "готово"},
                    "prompt_eval_count": 10,
                    "eval_count": 2,
                    "eval_duration": 1_000_000_000,
                }
            )
            return
        self.send_error(404)

    def _reply(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class ModelBenchmarkTests(unittest.TestCase):
    def test_dataset_is_fixed_and_covers_required_categories(self):
        tasks = benchmark.load_dataset(ROOT / "benchmarks" / "avelin_v1.jsonl")
        self.assertEqual(len(tasks), 36)
        self.assertEqual(len({task["id"] for task in tasks}), 36)
        self.assertEqual(
            {task["category"] for task in tasks},
            {"russian", "json", "tool_call", "agent", "context"},
        )

    def test_deterministic_judges(self):
        exact = {"judge": "exact", "expected": "Готово."}
        self.assertEqual(benchmark.score_response(exact, "готово", [])[0], 1)
        subset = {"judge": "json_subset", "expected": {"ok": True}}
        self.assertEqual(
            benchmark.score_response(subset, '{"ok": true, "extra": 1}', [])[0], 1
        )
        tool = {
            "judge": "tool_call",
            "expected": {"name": "read_file", "arguments": {"path": "notes.md"}},
        }
        calls = [{"function": {"name": "read_file", "arguments": {"path": "notes.md"}}}]
        self.assertEqual(benchmark.score_response(tool, "", calls)[0], 1)

    def test_latest_tag_satisfies_untagged_model_name(self):
        self.assertTrue(benchmark._model_available("phi4-mini", {"phi4-mini:latest"}))
        self.assertFalse(benchmark._model_available("phi4-mini", {"qwen3:1.7b"}))

    def test_ollama_contract_and_metadata(self):
        FakeOllamaHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            tags = benchmark.request_json(base_url, "/api/tags", None, 2)
            self.assertEqual(tags["models"][0]["name"], "test-model:latest")
            metadata = benchmark.model_metadata(base_url, "test-model:latest", 2, "")
            self.assertEqual(metadata["quantization_level"], "Q4_K_M")
            task = {
                "id": "fake-1",
                "category": "russian",
                "prompt": "Ответь: готово",
                "judge": "exact",
                "expected": "готово",
            }
            row = benchmark.benchmark_task(base_url, "test-model:latest", task, 2, "", 4096)
            self.assertEqual(row["score"], 1)
            chat = next(item for item in FakeOllamaHandler.requests if item["path"] == "/api/chat")
            self.assertEqual(chat["payload"]["options"]["temperature"], 0)
            self.assertEqual(chat["payload"]["options"]["seed"], 42)
            self.assertEqual(chat["payload"]["options"]["num_ctx"], 4096)
            self.assertEqual(chat["payload"]["options"]["num_predict"], 256)
            self.assertFalse(chat["payload"]["think"])
        finally:
            server.shutdown()
            server.server_close()

    def test_reports_are_generated_without_api_key(self):
        summaries = [
            {
                "model": "test-model",
                "score": 1.0,
                "category_scores": {"russian": 1.0},
                "passed": 1,
                "tasks": 1,
                "errors": 0,
                "median_latency_ms": 12.0,
                "mean_tokens_per_second": 5.0,
                "peak_vram_mib": 100,
                "manifest": {"gtx_1650_ti_4gb": "fits"},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            paths = benchmark.write_reports(
                Path(directory),
                ROOT / "benchmarks" / "avelin_v1.jsonl",
                summaries,
                {"test-model": []},
                "http://127.0.0.1:11434?token=secret-value",
                8192,
            )
            self.assertTrue(all(path.exists() for path in paths))
            combined = "\n".join(path.read_text(encoding="utf-8-sig") for path in paths)
            self.assertNotIn("secret-value", combined)


if __name__ == "__main__":
    unittest.main()
