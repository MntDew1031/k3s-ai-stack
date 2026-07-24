from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_callback():
    litellm = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    types_module = types.ModuleType("litellm.types")
    utils = types.ModuleType("litellm.types.utils")

    class CustomLogger:
        pass

    class ToolCall:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ModelResponseStream:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    custom_logger.CustomLogger = CustomLogger
    utils.ChatCompletionMessageToolCall = ToolCall
    utils.ChatCompletionDeltaToolCall = ToolCall
    utils.ModelResponseStream = ModelResponseStream
    sys.modules["litellm"] = litellm
    sys.modules["litellm.integrations"] = integrations
    sys.modules["litellm.integrations.custom_logger"] = custom_logger
    sys.modules["litellm.types"] = types_module
    sys.modules["litellm.types.utils"] = utils

    spec = importlib.util.spec_from_file_location(
        "tool_call_normalizer_test", ROOT / "litellm-tool-call-normalizer.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ToolCallNormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_callback()
        cls.request = {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "parameters": {
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ]
        }

    def test_parses_the_fenced_qwen_function_shape(self):
        parsed = self.module._parse_serialized_tool_call(
            '```json\n{"function":"web_search","parameters":{"query":"Cleveland weather"}}\n```',
            self.request,
        )
        self.assertEqual(parsed, ("web_search", '{"query":"Cleveland weather"}'))

    def test_parses_openai_style_name_and_json_arguments(self):
        parsed = self.module._parse_serialized_tool_call(
            '{"name":"web_search","arguments":"{\\"query\\":\\"weather\\"}"}',
            self.request,
        )
        self.assertEqual(parsed, ("web_search", '{"query":"weather"}'))

    def test_parses_declared_argumentless_tool_without_parameters(self):
        request = {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_current_timestamp",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        }
        parsed = self.module._parse_serialized_tool_call(
            '{"function":"get_current_timestamp"}', request
        )
        self.assertEqual(parsed, ("get_current_timestamp", "{}"))

    def test_rejects_missing_parameters_for_a_required_tool(self):
        self.assertIsNone(
            self.module._parse_serialized_tool_call('{"function":"web_search"}', self.request)
        )

    def test_parses_tool_json_after_a_leading_qwen_think_block(self):
        parsed = self.module._parse_serialized_tool_call(
            '<think>\ninternal reasoning\n</think>\n{"function":"web_search","parameters":{"query":"weather"}}',
            self.request,
        )
        self.assertEqual(parsed, ("web_search", '{"query":"weather"}'))

    def test_maps_qwen_google_search_alias_in_its_fenced_array_shape(self):
        parsed = self.module._parse_serialized_tool_call(
            'I will search for it.\n\n```json\n[{"name":"google_search","arguments":{"query":"Cleveland weather today"}}]\n```',
            self.request,
        )
        self.assertEqual(parsed, ("web_search", '{"query":"Cleveland weather today"}'))

    def test_parses_qwen_escaped_json_after_a_think_block(self):
        parsed = self.module._parse_serialized_tool_call(
            '<think>reasoning</think>\n{\\"function\\":\\"web_search\\",\\"parameters\\":{\\"query\\":\\"weather\\"}}',
            self.request,
        )
        self.assertEqual(parsed, ("web_search", '{"query":"weather"}'))

    def test_injects_declared_tool_protocol_once_for_local_backends(self):
        data = {
            **self.request,
            "messages": [{"role": "user", "content": "weather"}],
        }
        result = asyncio.run(
            self.module.ToolCallNormalizer().async_pre_call_hook(None, None, data, "acompletion")
        )
        self.assertIs(result, data)
        self.assertEqual(result["messages"][0]["role"], "system")
        self.assertIn("[local-tool-protocol-v1]", result["messages"][0]["content"])
        self.assertIn('"name":"web_search"', result["messages"][0]["content"])
        asyncio.run(
            self.module.ToolCallNormalizer().async_pre_call_hook(None, None, data, "acompletion")
        )
        self.assertEqual(len(data["messages"]), 2)

    def test_converts_completed_tool_messages_into_a_final_answer_turn(self):
        data = {
            **self.request,
            "tool_choice": "auto",
            "messages": [
                {"role": "system", "content": "[local-tool-protocol-v1]\nold protocol"},
                {"role": "user", "content": "What is the weather?"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
                {
                    "role": "tool",
                    "name": "web_search",
                    "tool_call_id": "call-1",
                    "content": "Sunny and 78F.",
                },
            ],
        }
        result = asyncio.run(
            self.module.ToolCallNormalizer().async_pre_call_hook(None, None, data, "acompletion")
        )
        self.assertIs(result, data)
        self.assertNotIn("tools", data)
        self.assertNotIn("tool_choice", data)
        self.assertIn("[local-tool-results-v1]", data["messages"][0]["content"])
        self.assertEqual(data["messages"][1]["content"], "What is the weather?")
        self.assertIn("Sunny and 78F.", data["messages"][-1]["content"])

    def test_rejects_search_alias_when_multiple_search_tools_are_declared(self):
        request = {
            "tools": [
                {"type": "function", "function": {"name": "web_search", "parameters": {"properties": {"query": {}}}}},
                {"type": "function", "function": {"name": "news_search", "parameters": {"properties": {"query": {}}}}},
            ]
        }
        self.assertIsNone(
            self.module._parse_serialized_tool_call(
                '[{"name":"google_search","arguments":{"query":"weather"}}]', request
            )
        )

    def test_rejects_prose_unknown_tools_and_invalid_arguments(self):
        self.assertIsNone(
            self.module._parse_serialized_tool_call("Please call web_search.", self.request)
        )
        self.assertIsNone(
            self.module._parse_serialized_tool_call(
                '{"function":"delete_all_memories","parameters":{}}', self.request
            )
        )
        self.assertIsNone(
            self.module._parse_serialized_tool_call(
                '{"function":"web_search","parameters":"not json"}', self.request
            )
        )

    def test_non_streaming_response_becomes_a_real_tool_call(self):
        message = types.SimpleNamespace(
            content='```json\n{"function":"web_search","parameters":{"query":"weather"}}\n```',
            tool_calls=None,
        )
        choice = types.SimpleNamespace(message=message, finish_reason="stop")
        response = types.SimpleNamespace(choices=[choice])
        result = asyncio.run(
            self.module.ToolCallNormalizer().async_post_call_success_hook(
                self.request, None, response
            )
        )
        self.assertIs(result, response)
        self.assertIsNone(message.content)
        self.assertEqual(message.tool_calls[0].function["name"], "web_search")
        self.assertEqual(message.tool_calls[0].function["arguments"], '{"query":"weather"}')
        self.assertEqual(choice.finish_reason, "tool_calls")

    def test_streaming_response_becomes_tool_call_chunks(self):
        async def stream():
            yield types.SimpleNamespace(
                id="chat-1",
                created=1,
                choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content='{"function":"web_'))],
            )
            yield types.SimpleNamespace(
                id="chat-1",
                created=1,
                choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content='search","parameters":{"query":"weather"}}'))],
            )

        async def collect():
            callback = self.module.ToolCallNormalizer()
            return [
                item
                async for item in callback.async_post_call_streaming_iterator_hook(
                    None, stream(), {**self.request, "model": "local-qwen"}
                )
            ]

        chunks = asyncio.run(collect())
        self.assertEqual(len(chunks), 2)
        tool = chunks[0].choices[0]["delta"]["tool_calls"][0]
        self.assertEqual(tool.function["name"], "web_search")
        self.assertEqual(chunks[1].choices[0]["finish_reason"], "tool_calls")

    def test_streaming_qwen_alias_shape_becomes_declared_tool_call(self):
        async def stream():
            for content in (
                "I will search.\n```json\n[",
                '{"name":"google_search","arguments":{"query":"weather"}}',
                "]\n```",
            ):
                yield types.SimpleNamespace(
                    id="chat-1",
                    created=1,
                    choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content))],
                )

        async def collect():
            return [
                item
                async for item in self.module.ToolCallNormalizer().async_post_call_streaming_iterator_hook(
                    None, stream(), {**self.request, "model": "local-qwen"}
                )
            ]

        chunks = asyncio.run(collect())
        tool = chunks[0].choices[0]["delta"]["tool_calls"][0]
        self.assertEqual(tool.function["name"], "web_search")

    def test_streaming_normal_answer_flushes_after_its_think_block(self):
        async def stream():
            for content in ("<think>checking sources</think>", " The forecast is sunny."):
                yield types.SimpleNamespace(
                    id="chat-1",
                    created=1,
                    choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content))],
                )

        async def collect():
            return [
                item
                async for item in self.module.ToolCallNormalizer().async_post_call_streaming_iterator_hook(
                    None, stream(), {**self.request, "model": "local-qwen"}
                )
            ]

        chunks = asyncio.run(collect())
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[1].choices[0].delta.content, " The forecast is sunny.")


if __name__ == "__main__":
    unittest.main()
