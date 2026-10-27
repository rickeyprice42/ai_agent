from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import json
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.agent import Agent
from ai_agent.config import Settings, load_project_secrets
from ai_agent.llm.base import ProviderCapabilities, ProviderDescriptor, ProviderError
from ai_agent.llm.failover import FailoverProvider
from ai_agent.llm.openai_provider import OpenAIProvider
from ai_agent.llm.vision import VisionRouter
from ai_agent.types import ModelResponse, ModelStreamChunk


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class StubProvider:
    def __init__(self, name: str, text: str = "", error: ProviderError | None = None):
        self._descriptor = ProviderDescriptor(name, name, ProviderCapabilities())
        self.text = text
        self.error = error

    @property
    def descriptor(self):
        return self._descriptor

    def generate(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return ModelResponse(text=self.text)

    def stream(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        yield ModelStreamChunk(delta=self.text)
        yield ModelStreamChunk(response=ModelResponse(text=self.text))


class StubVision:
    def __init__(self, name: str, model: str, text: str = "", fail: bool = False):
        self.name = name
        self.model = model
        self.text = text
        self.fail = fail

    def analyze(self, image: bytes, mime_type: str, prompt: str) -> str:
        if self.fail:
            raise ProviderError("offline", "unavailable")
        return self.text


def isolated_settings(root: Path) -> Settings:
    return replace(
        Settings.load(ROOT),
        model_provider="mock",
        model_name="mock-local",
        database_file=root / "avelin.sqlite3",
        memory_file=root / "memory.json",
        tool_workspace_dir=root / "workspace",
    )


class MultimodalFailoverTests(unittest.TestCase):
    def test_sectioned_secret_file_loads_both_keys_without_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "API_KEY.txt"
            path.write_text(
                "Avelin Ollama Cloud API:\nollama-secret\n\nAvelin OpenAI API:\nopenai-secret\n",
                encoding="utf-8",
            )
            secrets = load_project_secrets(path)
            self.assertEqual(secrets["OLLAMA_API_KEY"], "ollama-secret")
            self.assertEqual(secrets["OPENAI_API_KEY"], "openai-secret")

    def test_reasoning_provider_falls_back_before_any_stream_output(self):
        failed = StubProvider("beam", error=ProviderError("offline", "unavailable"))
        fallback = StubProvider("openai", text="fallback answer")
        provider = FailoverProvider([failed, fallback])
        self.assertEqual(provider.generate("", [], []).text, "fallback answer")
        chunks = list(provider.stream("", [], []))
        self.assertEqual("".join(item.delta for item in chunks), "fallback answer")

    def test_openai_responses_adapter_parses_text_and_tool_call(self):
        payload = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]},
                {"type": "function_call", "name": "get_time", "arguments": "{}"},
            ]
        }
        provider = OpenAIProvider(model="gpt-6-luna", api_key="test-key")
        with patch(
            "ai_agent.llm.openai_provider.request.urlopen",
            return_value=FakeResponse(json.dumps(payload).encode("utf-8")),
        ) as mocked:
            response = provider.generate("system", [], [{"name": "get_time", "schema": {"type": "object", "properties": {}}}])
        self.assertEqual(response.text, "ok")
        self.assertEqual(response.tool_calls[0].name, "get_time")
        sent = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(sent["model"], "gpt-6-luna")
        self.assertNotIn("test-key", json.dumps(sent))

    def test_vision_falls_back_and_cloud_upload_requires_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "screen.png").write_bytes(b"not-a-real-png-but-provider-is-stubbed")
            router = VisionRouter(
                workspace,
                [
                    StubVision("ollama_cloud", "gemma4:31b", fail=True),
                    StubVision("openai", "gpt-6-luna", text="Visible test window"),
                ],
            )
            result = router.analyze_path("screen.png", "Describe")
            self.assertEqual(result.provider, "openai")
            self.assertEqual(result.text, "Visible test window")

            agent = Agent(isolated_settings(root / "agent"), user_id="alice")
            tool = agent.tools.get("analyze_image")
            decision = agent.policy.authorize(
                tool, {"path": "screen.png", "prompt": "Describe"}
            )
            self.assertEqual(decision.outcome, "approval_required")


if __name__ == "__main__":
    unittest.main()
