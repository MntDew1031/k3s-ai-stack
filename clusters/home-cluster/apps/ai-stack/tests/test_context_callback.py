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

    class CustomLogger:
        pass

    custom_logger.CustomLogger = CustomLogger
    sys.modules["litellm"] = litellm
    sys.modules["litellm.integrations"] = integrations
    sys.modules["litellm.integrations.custom_logger"] = custom_logger

    path = ROOT / "litellm-context-callback.py"
    spec = importlib.util.spec_from_file_location("context_callback_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeCallback:
    def __init__(self, base, summary="preserved checkpoint"):
        self._base = base
        self.summary = summary
        self.calls = []
        self.checkpoints = {}

    async def _safe_summarize(self, model, messages, context_limit):
        self.calls.append((model, messages, context_limit))
        return self.summary

    async def _compact_sources(self, model, messages, context_limit):
        method = self._base.ContextCompactionCallback._compact_sources
        return await method(self, model, messages, context_limit)

    async def _save_checkpoint(self, checkpoint_key, model, summary):
        self.checkpoints[checkpoint_key] = summary

    async def _load_checkpoint(self, checkpoint_key):
        return self.checkpoints.get(checkpoint_key)

    async def hook(self, data):
        method = self._base.ContextCompactionCallback.async_pre_call_hook
        return await method(self, None, None, data, "completion")


class ContextCallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_callback()

    def test_manual_checkpoint_discards_pre_checkpoint_dialogue(self):
        messages = [
            {"role": "system", "content": "privacy first"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "/compact"},
            {"role": "assistant", "content": "[Context compacted]\nGoal: keep local"},
            {"role": "user", "content": "continue"},
        ]
        result = self.module._apply_manual_checkpoint(messages)
        text = "\n".join(str(message["content"]) for message in result)
        self.assertIn("privacy first", text)
        self.assertIn("Goal: keep local", text)
        self.assertIn("continue", text)
        self.assertNotIn("old question", text)

    def test_manual_command_uses_the_exact_selected_model(self):
        callback = FakeCallback(self.module)
        data = {
            "model": "SP-qwen3.6:35b",
            "messages": [
                {"role": "system", "content": "stay private"},
                {"role": "user", "content": "important history"},
                {"role": "assistant", "content": "work completed"},
                {"role": "user", "content": "/compact"},
            ],
        }
        result = asyncio.run(callback.hook(data))
        self.assertEqual(callback.calls[0][0], "SP-qwen3.6:35b")
        self.assertEqual(result["model"], "SP-qwen3.6:35b")
        self.assertIn("[Context compacted]", result["mock_response"])
        self.assertNotIn("important history", result["mock_response"])
        self.assertEqual(result["messages"][-1]["content"], "/compact")
        self.assertNotIn("context-checkpoint-b64", result["mock_response"])
        self.assertIn("preserved checkpoint", callback.checkpoints.values())

    def test_only_unspaced_compact_command_is_recognized(self):
        self.assertTrue(
            self.module._is_compact_command({"role": "user", "content": "/compact"})
        )
        self.assertFalse(
            self.module._is_compact_command({"role": "user", "content": "/ compact"})
        )

    def test_oversized_web_sources_are_compacted_before_dispatch(self):
        callback = FakeCallback(self.module, summary="Source 1: exact local facts")
        source = "<source id=\"1\" name=\"example\">" + ("fact " * 22000) + "</source>"
        data = {
            "model": "SP-qwen3.6:35b",
            "messages": [
                {"role": "system", "content": "answer with citations"},
                {"role": "user", "content": f"Find the answer.\n\n{source}"},
            ],
        }
        before = self.module._estimate_request_tokens(data, data["messages"])
        result = asyncio.run(callback.hook(data))
        after = self.module._estimate_request_tokens(result, result["messages"])
        self.assertGreater(before, 32768)
        self.assertLess(after, before)
        self.assertIn("Find the answer.", result["messages"][-1]["content"])
        self.assertIn("<compacted_sources>", result["messages"][-1]["content"])
        self.assertIn("exact local facts", result["messages"][-1]["content"])
        self.assertEqual(callback.calls[0][0], "SP-qwen3.6:35b")

    def test_future_model_uses_default_limit_and_exact_selected_model(self):
        callback = FakeCallback(self.module)
        data = {
            "model": "future-local-model",
            "messages": [
                {"role": "user", "content": "important history"},
                {"role": "assistant", "content": "work completed"},
                {"role": "user", "content": "/compact"},
            ],
        }
        result = asyncio.run(callback.hook(data))
        self.assertEqual(callback.calls[0][0], "future-local-model")
        self.assertEqual(callback.calls[0][2], 32768)
        self.assertEqual(result["model"], "future-local-model")
        self.assertIn("[Context compacted]", result["mock_response"])

    def test_non_sp_model_manual_checkpoint_is_reused_on_next_turn(self):
        callback = FakeCallback(self.module, summary="local model checkpoint")
        history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "/compact"},
        ]
        first = {
            "model": "qwen3.5:4b",
            "messages": list(history),
        }
        compacted = asyncio.run(callback.hook(first))
        follow_up = {
            "model": "qwen3.5:4b",
            "messages": history + [
                {"role": "assistant", "content": compacted["mock_response"]},
                {"role": "user", "content": "continue"},
            ],
        }
        result = asyncio.run(callback.hook(follow_up))
        joined = "\n".join(str(message["content"]) for message in result["messages"])
        self.assertIn("local model checkpoint", joined)
        self.assertIn("continue", joined)
        self.assertNotIn("old question", joined)

    def test_model_metadata_overrides_the_future_model_default(self):
        data = {
            "model_info": {"max_input_tokens": 65536},
        }
        self.assertEqual(self.module._context_limit(data, "future-model"), 65536)

    def test_internal_summary_request_cannot_recurse(self):
        callback = FakeCallback(self.module)
        data = {
            "model": "SP-qwen3.6:35b",
            "metadata": {"context_compaction_internal": True},
            "messages": [{"role": "user", "content": "/compact"}],
        }
        result = asyncio.run(callback.hook(data))
        self.assertIs(result, data)
        self.assertEqual(callback.calls, [])

        for marker in (
            {"litellm_metadata": {"context_compaction_internal": True}},
            {"user": "__context_compaction_internal__"},
        ):
            marked = {
                "model": "SP-qwen3.6:35b",
                "messages": [{"role": "user", "content": "/compact"}],
                **marker,
            }
            result = asyncio.run(callback.hook(marked))
            self.assertIs(result, marked)
            self.assertEqual(callback.calls, [])

    def test_recent_split_keeps_tool_result_with_its_user_turn(self):
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "run tool"},
            {"role": "assistant", "content": "tool call", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "tool result"},
        ]
        _, older, recent = self.module._split_old_and_recent(messages, 100)
        self.assertEqual([item["content"] for item in older], ["old", "old reply"])
        self.assertEqual(
            [item["content"] for item in recent],
            ["run tool", "tool call", "tool result"],
        )


if __name__ == "__main__":
    unittest.main()
