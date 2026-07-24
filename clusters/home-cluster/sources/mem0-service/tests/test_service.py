from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


def load_main():
    package = types.ModuleType("mem0_service_test")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package

    memory_stub = types.ModuleType("mem0_service_test.memory")
    memory_stub.get_memory = lambda: None
    sys.modules[memory_stub.__name__] = memory_stub

    context_store_stub = types.ModuleType("mem0_service_test.context_store")
    context_store_stub.get_context_checkpoint = lambda _key: None
    context_store_stub.put_context_checkpoint = lambda *_args: None
    sys.modules[context_store_stub.__name__] = context_store_stub

    spec = importlib.util.spec_from_file_location("mem0_service_test.main", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_memory():
    mem0_stub = types.ModuleType("mem0")
    mem0_stub.Memory = object
    sys.modules["mem0"] = mem0_stub
    spec = importlib.util.spec_from_file_location("mem0_memory_test", ROOT / "memory.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeMemory:
    def __init__(self):
        self.deleted = []
        self.updated = []

    def get_all(self, **kwargs):
        self.list_kwargs = kwargs
        records = [] if self.deleted else [{"id": "alpha-1"}, {"id": "alpha-2"}]
        return {"results": records}

    def search(self, query, **kwargs):
        self.search_call = (query, kwargs)
        return {"results": [{"id": "alpha-1", "score": 0.9}]}

    def delete(self, memory_id):
        self.deleted.append(memory_id)

    def update(self, memory_id, **kwargs):
        self.updated.append((memory_id, kwargs))
        return memory_id

    def get(self, memory_id):
        return {"id": memory_id, "metadata": {"category": "project"}}


class FakeCursor:
    def __init__(self):
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.query = query

    def fetchall(self):
        return [("agent_id", "hermes", 3), ("user_id", "opencode", 12)]


class FakeVectorStore:
    collection_name = "mem0_memories"

    def __init__(self):
        self.cursor = FakeCursor()

    def _get_cursor(self):
        return self.cursor


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_main()

    def test_ui_mount_is_last_and_api_routes_remain_reachable(self):
        paths = [route.path for route in self.main.app.routes]
        self.assertEqual(paths[-1], "")
        self.assertLess(paths.index("/memories"), len(paths) - 1)
        self.assertLess(paths.index("/memories/delete_many"), len(paths) - 1)
        self.assertLess(paths.index("/memories/delete_duplicates"), len(paths) - 1)
        self.assertLess(paths.index("/memories/delete_all"), len(paths) - 1)
        self.assertLess(paths.index("/context-checkpoints/{checkpoint_key}"), len(paths) - 1)

    def test_auth_fails_closed_and_accepts_header_key(self):
        previous = os.environ.get("MEM0_API_KEY")
        try:
            os.environ["MEM0_API_KEY"] = "expected"
            asyncio.run(self.main.require_key(x_api_key="expected"))
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(self.main.require_key(x_api_key="wrong"))
            self.assertEqual(raised.exception.status_code, 401)
        finally:
            if previous is None:
                os.environ.pop("MEM0_API_KEY", None)
            else:
                os.environ["MEM0_API_KEY"] = previous

    def test_delete_all_deletes_only_listed_ids_and_never_calls_sdk_delete_all(self):
        fake = FakeMemory()
        self.main.get_memory = lambda: fake
        result = asyncio.run(self.main.delete_all(self.main.DeleteAllRequest(user_id="alpha")))
        self.assertEqual(fake.deleted, ["alpha-1", "alpha-2"])
        self.assertEqual(fake.list_kwargs, {"filters": {"user_id": "alpha"}, "top_k": 1000})
        self.assertEqual(result["deleted_count"], 2)

    def test_delete_all_requires_a_scope(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.main.delete_all(self.main.DeleteAllRequest()))
        self.assertEqual(raised.exception.status_code, 422)

    def test_delete_many_validates_scope_then_deletes_selected_ids(self):
        fake = FakeMemory()
        self.main.get_memory = lambda: fake
        result = asyncio.run(
            self.main.delete_many(
                self.main.DeleteManyRequest(
                    user_id="alpha",
                    memory_ids=["alpha-2", "alpha-2"],
                )
            )
        )
        self.assertEqual(fake.deleted, ["alpha-2"])
        self.assertEqual(fake.list_kwargs, {"filters": {"user_id": "alpha"}, "top_k": 1000})
        self.assertEqual(result["deleted_ids"], ["alpha-2"])
        self.assertEqual(result["deleted_count"], 1)

    def test_delete_many_rejects_stale_or_out_of_scope_selection_without_deleting(self):
        fake = FakeMemory()
        self.main.get_memory = lambda: fake
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                self.main.delete_many(
                    self.main.DeleteManyRequest(
                        agent_id="hermes",
                        memory_ids=["alpha-1", "not-in-scope"],
                    )
                )
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(fake.deleted, [])

    def test_delete_many_requires_a_scope(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                self.main.delete_many(
                    self.main.DeleteManyRequest(memory_ids=["alpha-1"])
                )
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_delete_duplicates_keeps_newest_normalized_exact_copy(self):
        class DuplicateMemory(FakeMemory):
            def get_all(self, **kwargs):
                self.list_kwargs = kwargs
                return {
                    "results": [
                        {
                            "id": "older-copy",
                            "memory": "The operator prefers encrypted secrets.",
                            "created_at": "2026-07-01T12:00:00Z",
                        },
                        {
                            "id": "newer-copy",
                            "memory": "the operator   prefers encrypted secrets.",
                            "updated_at": "2026-07-02T12:00:00Z",
                        },
                        {
                            "id": "different-fact",
                            "memory": "The operator prefers ephemeral secrets for throwaway tests.",
                        },
                    ]
                }

        fake = DuplicateMemory()
        self.main.get_memory = lambda: fake
        result = asyncio.run(
            self.main.delete_duplicates(self.main.DeleteAllRequest(user_id="alpha"))
        )
        self.assertEqual(fake.deleted, ["older-copy"])
        self.assertEqual(result["deleted_ids"], ["older-copy"])
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["duplicate_groups"], 1)
        self.assertEqual(fake.list_kwargs, {"filters": {"user_id": "alpha"}, "top_k": 1000})

    def test_delete_duplicates_requires_a_scope(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.main.delete_duplicates(self.main.DeleteAllRequest()))
        self.assertEqual(raised.exception.status_code, 422)

    def test_mem0_2_list_and_search_use_filters_and_top_k(self):
        fake = FakeMemory()
        self.main.get_memory = lambda: fake

        listed = asyncio.run(self.main.list_memories(user_id="alpha", limit=37))
        searched = asyncio.run(
            self.main.search_memories(
                self.main.SearchRequest(query="deployment details", agent_id="hermes", limit=8)
            )
        )

        self.assertEqual(fake.list_kwargs, {"filters": {"user_id": "alpha"}, "top_k": 37})
        self.assertEqual(
            fake.search_call,
            ("deployment details", {"filters": {"agent_id": "hermes"}, "top_k": 8}),
        )
        self.assertEqual(len(listed["result"]["results"]), 2)
        self.assertEqual(searched["result"]["results"][0]["score"], 0.9)

    def test_update_preserves_custom_metadata(self):
        fake = FakeMemory()
        result = self.main._update_preserving_metadata(fake, "m-1", "updated text")
        self.assertEqual(result, "m-1")
        self.assertEqual(
            fake.updated,
            [("m-1", {"text": "updated text", "metadata": {"category": "project"}})],
        )

    def test_scope_discovery_returns_counts_without_selecting_vectors(self):
        memory = types.SimpleNamespace(vector_store=FakeVectorStore())
        scopes = self.main._discover_scopes(memory)
        self.assertEqual(
            scopes,
            [
                {"type": "agent_id", "value": "hermes", "count": 3},
                {"type": "user_id", "value": "opencode", "count": 12},
            ],
        )
        self.assertIn("payload->>'user_id'", memory.vector_store.cursor.query)
        self.assertNotIn("SELECT id, vector", memory.vector_store.cursor.query)

    def test_scope_discovery_rejects_an_unsafe_collection_name(self):
        vector_store = FakeVectorStore()
        vector_store.collection_name = "memories; DROP TABLE memories"
        with self.assertRaises(ValueError):
            self.main._discover_scopes(types.SimpleNamespace(vector_store=vector_store))

    def test_context_checkpoint_round_trip_uses_separate_store(self):
        stored = {}

        def put(key, model, summary, token_count):
            stored[key] = {
                "checkpoint_key": key,
                "model": model,
                "summary": summary,
                "token_count": token_count,
            }
            return stored[key]

        self.main.put_context_checkpoint = put
        self.main.get_context_checkpoint = stored.get
        key = "a" * 64
        saved = asyncio.run(
            self.main.save_context_checkpoint(
                key,
                self.main.ContextCheckpointRequest(
                    model="qwen3.5:4b", summary="private checkpoint", token_count=42
                ),
            )
        )
        loaded = asyncio.run(self.main.load_context_checkpoint(key))
        self.assertTrue(saved["ok"])
        self.assertEqual(loaded["checkpoint"]["summary"], "private checkpoint")

    def test_context_checkpoint_rejects_unhashed_keys(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.main.load_context_checkpoint("chat-id-in-plain-text"))
        self.assertEqual(raised.exception.status_code, 422)


class MemoryConfigTests(unittest.TestCase):
    def test_default_extraction_prompt_is_reachable(self):
        memory = load_memory()
        required = {
            "MEM0_PG_HOST": "db",
            "MEM0_PG_USER": "mem0",
            "MEM0_PG_PASSWORD": "secret",
            "MEM0_LLM_BASE_URL": "http://litellm/v1",
            "MEM0_LLM_API_KEY": "key",
            "MEM0_OLLAMA_BASE_URL": "http://ollama",
        }
        previous = {key: os.environ.get(key) for key in required}
        previous_prompt = os.environ.pop("MEM0_CUSTOM_EXTRACTION_PROMPT", None)
        try:
            os.environ.update(required)
            config = memory.build_config()
            self.assertEqual(config["custom_instructions"], memory.DEFAULT_EXTRACTION_PROMPT)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if previous_prompt is not None:
                os.environ["MEM0_CUSTOM_EXTRACTION_PROMPT"] = previous_prompt


if __name__ == "__main__":
    unittest.main()
