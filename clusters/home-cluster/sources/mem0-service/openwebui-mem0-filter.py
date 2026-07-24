"""
title: mem0 Memory
author: overtime-outfitters
version: 0.2.0
required_open_webui_version: 0.5.0
description: >
  Long-term memory for OpenWebUI backed by the self-hosted mem0 service.
  On each turn it searches mem0 for relevant memories and injects them as
  context, then stores only the user's new message so mem0 can extract durable
  facts without re-learning assistant replies that repeat injected memories.

HOW TO INSTALL (do this in BOTH OpenWebUI instances):
  1. OpenWebUI -> Admin Panel -> Functions -> "+" (Add Function).
  2. Paste this whole file, Save.
  3. Toggle it on (globally, or per-model).
  4. Click the gear to set Valves:
       - mem0_api_key : the MEM0_API_KEY value from the mem0-secrets Secret.
       - mem0_base_url:
           * cluster OpenWebUI  -> http://mem0.ai-stack.svc.cluster.local:8000
           * shadow12 gaming PC -> http://<any-node-LAN-ip>:31050
  This file is NOT a Kubernetes manifest; it is pasted into the UI, which
  stores it in the OpenWebUI database.

Memory is scoped per OpenWebUI user (by email, else user id), namespaced as
"owui:<id>" so it never collides with agent memories.
"""

import asyncio
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        enabled: bool = Field(default=True, description="Master on/off switch.")
        mem0_base_url: str = Field(
            default="http://mem0.ai-stack.svc.cluster.local:8000",
            description="mem0 service base URL. Cluster: the svc DNS above. LAN/shadow12: http://<node-ip>:31050",
        )
        mem0_api_key: str = Field(
            default="",
            description="MEM0_API_KEY bearer token from the mem0-secrets Secret.",
        )
        max_memories: int = Field(
            default=5, description="How many memories to inject per turn."
        )
        store_conversations: bool = Field(
            default=True, description="Store each turn so mem0 can learn from it."
        )
        # mem0's /memories endpoint runs synchronous LLM extraction and
        # routinely takes 5-15s, occasionally longer with cold ollama models.
        # 10s loses the race, 60s gives real headroom. Search hits are still
        # fast under this budget.
        timeout_seconds: int = Field(default=60)

    def __init__(self):
        self.valves = self.Valves()

    # ---- helpers ----
    def _user_id(self, __user__: Optional[dict]) -> Optional[str]:
        if not __user__:
            return None
        uid = __user__.get("email") or __user__.get("id")
        return f"owui:{uid}" if uid else None

    @staticmethod
    def _mark_mem0_managed(body: dict) -> None:
        """Prevent LiteLLM's optional callback from handling this turn too."""
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["mem0_openwebui_filter"] = True
        body["metadata"] = metadata

    @staticmethod
    def _as_text(content) -> str:
        # OpenWebUI content can be a plain string or a list of multimodal parts.
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        return ""

    async def _post(self, path: str, payload: dict) -> dict:
        headers = {
            "X-API-Key": self.valves.mem0_api_key,
            "Content-Type": "application/json",
        }
        # Per-path floors. /search runs in the inlet hot path (user is waiting
        # on it before the LLM call goes out) so we want a tight ceiling.
        # /memories runs in a fire-and-forget background task so we can be
        # patient with it: mem0 extraction makes 1 + N LLM calls (1 to extract
        # facts, 1 per fact to decide ADD/UPDATE/NONE), which under any
        # backlog can take several minutes. 10 minutes is plenty.
        if "/memories" in path:
            min_floor = 600
        else:
            min_floor = 30
        effective_timeout = max(int(self.valves.timeout_seconds or 0), min_floor)
        print(
            f"[mem0-filter] _post path={path} effective_timeout={effective_timeout} "
            f"valve={self.valves.timeout_seconds!r}",
            flush=True,
        )
        timeout = aiohttp.ClientTimeout(total=effective_timeout)
        url = self.valves.mem0_base_url.rstrip("/") + path
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json()

    # ---- before the LLM call: inject relevant memories ----
    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        print(f"[mem0-filter] inlet called; user_keys={list((__user__ or {}).keys())}", flush=True)
        if not self.valves.enabled or not self.valves.mem0_api_key:
            print("[mem0-filter] inlet: disabled or no api key, skipping", flush=True)
            return body
        user_id = self._user_id(__user__)
        if not user_id:
            print("[mem0-filter] inlet: no user_id derivable, skipping", flush=True)
            return body
        self._mark_mem0_managed(body)

        messages = body.get("messages", [])
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )
        if not last_user:
            return body
        query = self._as_text(last_user.get("content", "")).strip()
        if not query:
            return body

        try:
            data = await self._post(
                "/search",
                {"query": query, "user_id": user_id, "limit": self.valves.max_memories},
            )
        except Exception:
            # Never let a memory hiccup block the chat.
            return body

        result = data.get("result", {})
        mems = result.get("results", result) if isinstance(result, dict) else result
        lines = []
        for m in mems or []:
            text = m.get("memory") if isinstance(m, dict) else str(m)
            if text:
                lines.append(f"- {text}")
        if not lines:
            return body

        context = "Relevant things you remember about the user:\n" + "\n".join(lines)
        messages.insert(0, {"role": "system", "content": context})
        body["messages"] = messages
        return body

    # ---- after the LLM responds: store the turn ----
    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        print(f"[mem0-filter] outlet called; user_keys={list((__user__ or {}).keys())}", flush=True)
        if (
            not self.valves.enabled
            or not self.valves.mem0_api_key
            or not self.valves.store_conversations
        ):
            print("[mem0-filter] outlet: disabled / no key / store off, skipping", flush=True)
            return body
        user_id = self._user_id(__user__)
        if not user_id:
            print("[mem0-filter] outlet: no user_id derivable, skipping", flush=True)
            return body

        messages = body.get("messages", [])
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )
        # Do not store the assistant reply. It can restate the context injected
        # in inlet, which turns one durable fact into duplicate Mem0 writes.
        to_store = []
        if last_user:
            text = self._as_text(last_user.get("content", "")).strip()
            if text:
                to_store.append({"role": "user", "content": text})
        print(f"[mem0-filter] outlet: to_store={len(to_store)} messages, user_id={user_id}", flush=True)
        if not to_store:
            return body

        # Fire-and-forget. mem0's extraction can take 30-90s (it makes one
        # LLM call to pull facts, then one more per extracted fact to decide
        # ADD/UPDATE/NONE). The user does NOT need their next turn gated on
        # that. Schedule the POST as a background task and let outlet return
        # immediately so OpenWebUI never blocks and a backlog never forms.
        async def _bg_store(payload: dict):
            try:
                r = await self._post("/memories", payload)
                print(
                    f"[mem0-filter] bg store ok user_id={user_id} keys={list((r or {}).keys())}",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[mem0-filter] bg store failed user_id={user_id}: {type(e).__name__}: {e}",
                    flush=True,
                )

        asyncio.create_task(
            _bg_store({"messages": to_store, "user_id": user_id, "infer": True,
                       "metadata": {"source": "openwebui-filter", "capture": "user-only"}})
        )
        return body
