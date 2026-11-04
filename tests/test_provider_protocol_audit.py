from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_agent.llm.base import ProviderError
from ai_agent.llm.openai_provider import OpenAIProvider
from ai_agent.llm.ollama_provider import OllamaProvider
from ai_agent.types import Message, ToolCall
from ai_agent.config import Settings
from ai_agent.agent import Agent
from ai_agent.llm.registry import build_provider_registry
from ai_agent.llm.vision import OpenAIVisionProvider


TOOLS = [{
    "name": "read_file",
    "schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path"],
    },
}]


def event(kind: str, **fields) -> bytes:
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n").encode()


class ProviderProtocolAudit(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAIProvider(api_key="test")

    def test_all_production_function_schemas_define_array_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(Settings.load(Path(__file__).resolve().parents[1]), model_provider="mock", model_name="mock-local", database_file=root / "data.sqlite", memory_file=root / "memory.json", tool_workspace_dir=root / "workspace")
            functions = self.provider._payload("", [], Agent(settings).tools.list_for_model())["tools"]
        self.assertGreater(len(functions), 20)

        def walk(value, path):
            if isinstance(value, dict):
                if value.get("type") == "array":
                    self.assertIn("items", value, path)
                for key, item in value.items():
                    walk(item, path + "/" + key)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, path + "/" + str(index))

        walk(functions, "functions")

    def test_primary_openai_respects_selected_model_and_fallback_keeps_configured_model(self):
        settings = Settings.load(Path(__file__).resolve().parents[1])
        primary = build_provider_registry(replace(settings, model_provider="openai", model_name="selected-model", openai_model="fallback-model")).create("openai")
        fallback = build_provider_registry(replace(settings, model_provider="ollama_cloud", model_name="beam", openai_model="fallback-model")).create("openai")
        self.assertEqual(primary.model, "selected-model")
        self.assertEqual(fallback.model, "fallback-model")

    def test_vision_payload_does_not_force_unsupported_reasoning_effort(self):
        provider = OpenAIVisionProvider(model="selected-model", api_key="test")
        with patch("ai_agent.llm.vision._json_request", return_value={"output_text": "image content"}) as request:
            self.assertEqual(provider.analyze(b"fake-image", "image/png", "Describe"), "image content")
        payload = request.call_args.args[1]
        self.assertNotIn("reasoning", payload)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "selected-model")

    def test_strict_schema_matches_responses_requirements_and_preserves_optional_semantics(self):
        payload = self.provider._payload("", [], TOOLS)
        function = payload["tools"][0]
        self.assertTrue(function["strict"])
        self.assertFalse(function["parameters"]["additionalProperties"])
        self.assertEqual(set(function["parameters"]["required"]), {"path", "limit"})
        self.assertIn({"type": "null"}, function["parameters"]["properties"]["limit"]["anyOf"])
        self.assertNotIn("additionalProperties", TOOLS[0]["schema"])
        parsed = self.provider._model_response({"output": [{"type": "function_call", "name": "read_file", "call_id": "call_1", "arguments": '{"path":"a.txt","limit":null}'}]}, TOOLS)
        self.assertEqual(parsed.tool_calls[0].arguments, {"path": "a.txt"})

    def test_open_maps_use_explicit_non_strict_mode(self):
        tools = [{"name": "request", "schema": {"type": "object", "properties": {"headers": {"type": "object", "additionalProperties": {"type": "string"}}}}}]
        function = self.provider._payload("", [], tools)["tools"][0]
        self.assertFalse(function["strict"])
        self.assertEqual(function["parameters"], tools[0]["schema"])

    def test_native_function_ids_and_reasoning_roundtrip(self):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
        messages = [
            Message("assistant", "", tool_calls=[ToolCall("read_file", {"path": "a.txt"}, "call_42")], provider_items=[reasoning]),
            Message("tool", "untrusted contents", name="read_file", tool_call_id="call_42"),
        ]
        items = self.provider._payload("", messages, TOOLS)["input"]
        self.assertEqual(items[0], reasoning)
        self.assertEqual(items[1]["type"], "function_call")
        self.assertEqual(items[1]["call_id"], "call_42")
        self.assertEqual(items[2], {"type": "function_call_output", "call_id": "call_42", "output": "untrusted contents"})

    def test_real_sse_yields_before_completion_and_closes_on_cancel(self):
        body = io.BytesIO(event("response.output_text.delta", delta="first") + event("response.completed", response={"output": [{"type": "message", "content": [{"type": "output_text", "text": "first"}]}]}))
        with patch("ai_agent.llm.openai_provider.request.urlopen", return_value=body) as call:
            stream = self.provider.stream("", [Message("user", "hi")], [])
            self.assertEqual(next(stream).delta, "first")
            self.assertLess(body.tell(), len(body.getvalue()))
            stream.close()
        self.assertTrue(body.closed)
        self.assertTrue(json.loads(call.call_args.args[0].data)["stream"])

    def test_sse_requires_terminal_completion(self):
        body = io.BytesIO(event("response.output_text.delta", delta="partial"))
        with patch("ai_agent.llm.openai_provider.request.urlopen", return_value=body):
            with self.assertRaises(ProviderError) as caught:
                list(self.provider.stream("", [], []))
        self.assertEqual(caught.exception.code, "incomplete_stream")

    def test_sse_parses_completed_function_call(self):
        body = io.BytesIO(event("response.completed", response={"output": [{"type": "function_call", "name": "read_file", "call_id": "call_42", "arguments": '{"path":"a.txt"}'}]}))
        with patch("ai_agent.llm.openai_provider.request.urlopen", return_value=body):
            chunks = list(self.provider.stream("", [], TOOLS))
        self.assertEqual(chunks[-1].response.tool_calls[0].call_id, "call_42")

    def test_malformed_tool_arguments_are_not_executed_as_empty_objects(self):
        with self.assertRaises(ProviderError) as caught:
            self.provider._model_response({"output": [{"type": "function_call", "name": "read_file", "arguments": "{broken"}]})
        self.assertEqual(caught.exception.code, "invalid_tool_call")
        with self.assertRaises(ProviderError) as caught:
            OllamaProvider()._tool_call({"function": {"name": "read_file", "arguments": "{broken"}})
        self.assertEqual(caught.exception.code, "invalid_tool_call")

    def test_ollama_keeps_assistant_tool_call_protocol(self):
        payload = OllamaProvider()._payload("", [Message("assistant", "", tool_calls=[ToolCall("read_file", {"path": "a.txt"}, "c")])], TOOLS, False)
        self.assertEqual(payload["messages"][1]["tool_calls"][0]["function"]["arguments"], {"path": "a.txt"})

    def test_malformed_ollama_function_does_not_escape_as_attribute_error(self):
        with self.assertRaises(ProviderError) as caught:
            OllamaProvider()._merge_tool_calls({}, [{"function": "broken"}])
        self.assertEqual(caught.exception.code, "invalid_tool_call")


if __name__ == "__main__":
    unittest.main()
