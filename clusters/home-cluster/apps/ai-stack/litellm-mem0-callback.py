"""LiteLLM custom callback that wires every chat completion through mem0.

OPT-IN per virtual key. The callback only fires when the calling LiteLLM
virtual key has `mem0_enabled: true` in its metadata. mem0's own extraction
LLM calls use the LITELLM_MASTER_KEY (which has no such metadata), so they
bypass this hook and the obvious loop (mem0 -> litellm -> mem0 -> ...) is
broken cleanly.

How to use:
  1. In the LiteLLM UI, create a virtual key for the consumer (e.g. OpenCode)
     with metadata:
         {"mem0_enabled": true, "mem0_scope": "opencode"}
  2. Point the consumer at that key.
  3. Memory injection on inlet and user-authored fact storage on outlet happen
     automatically. Assistant replies are deliberately not stored because they
     can repeat injected memories.

Scope priority (highest first):
  1. `data["user"]`                            (OpenAI request `user` field)
  2. `user_api_key_dict.metadata["mem0_scope"]`
  3. `user_api_key_dict.key_alias`
  4. "default"

Wired in via litellm config.yaml:
    litellm_settings:
      callbacks: ["mem0_callback.proxy_handler_instance"]

Env:
  MEM0_BASE_URL   default http://mem0.ai-stack.svc.cluster.local:8000
  MEM0_API_KEY    must equal MEM0_API_KEY in ai-stack mem0-secrets
  MEM0_INJECT_MAX default 5
  MEM0_TIMEOUT_S  default 5
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from litellm.integrations.custom_logger import CustomLogger


_MEM0_BASE = os.environ.get(
    "MEM0_BASE_URL", "http://mem0.ai-stack.svc.cluster.local:8000"
).rstrip("/")
_MEM0_KEY = os.environ.get("MEM0_API_KEY", "")
_MAX_MEMS = int(os.environ.get("MEM0_INJECT_MAX", "5"))
_TIMEOUT_SEC = float(os.environ.get("MEM0_TIMEOUT_S", "5"))


def _enabled(user_api_key_dict) -> bool:
    """Only run for virtual keys explicitly opted in via metadata.mem0_enabled."""
    if not _MEM0_KEY:
        return False
    md = getattr(user_api_key_dict, "metadata", None) or {}
    return bool(md.get("mem0_enabled"))


def _scope(data: dict, user_api_key_dict) -> str:
    md = getattr(user_api_key_dict, "metadata", None) or {}
    return (
        data.get("user")
        or md.get("mem0_scope")
        or getattr(user_api_key_dict, "key_alias", None)
        or "default"
    )


def _as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _managed_by_openwebui(*containers: Any) -> bool:
    """Avoid a second Mem0 pass when OpenWebUI's filter already owns a turn."""
    for container in containers:
        if not isinstance(container, dict):
            continue
        if container.get("mem0_openwebui_filter"):
            return True
        metadata = container.get("metadata")
        if isinstance(metadata, dict) and metadata.get("mem0_openwebui_filter"):
            return True
    return False


class Mem0Callback(CustomLogger):
    """Pre-call: inject relevant memories. Post-call: store the turn."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict,
        cache,
        data: dict,
        call_type: str,
    ) -> Optional[dict]:
        if call_type not in ("completion", "acompletion"):
            return data
        if _managed_by_openwebui(data):
            return data
        if not _enabled(user_api_key_dict):
            return data
        messages = data.get("messages", [])
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )
        if not last_user:
            return data
        query = _as_text(last_user.get("content", "")).strip()
        if not query:
            return data
        scope = _scope(data, user_api_key_dict)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
                r = await client.post(
                    f"{_MEM0_BASE}/search",
                    json={"query": query, "user_id": scope, "limit": _MAX_MEMS},
                    headers={"X-API-Key": _MEM0_KEY},
                )
                r.raise_for_status()
                payload = r.json()
        except Exception:
            # Never block the chat on a memory hiccup.
            return data

        result = payload.get("result", {})
        mems = result.get("results", result) if isinstance(result, dict) else result
        lines = [
            f"- {m.get('memory')}"
            for m in (mems or [])
            if isinstance(m, dict) and m.get("memory")
        ]
        if not lines:
            return data
        context = "Relevant memories about the user:\n" + "\n".join(lines)
        messages.insert(0, {"role": "system", "content": context})
        data["messages"] = messages
        return data

    async def async_log_success_event(
        self,
        kwargs: dict,
        response_obj,
        start_time,
        end_time,
    ) -> None:
        """Fires AFTER a successful completion, for BOTH streaming and
        non-streaming. We use this instead of async_post_call_success_hook
        because that hook only fires on non-streaming, and modern clients
        (OpenCode, Cursor, etc.) almost always stream, so the previous hook
        choice meant the outlet never ran for them.
        """
        # Only chat completions, not embeddings or others.
        call_type = kwargs.get("call_type", "")
        if call_type not in ("completion", "acompletion"):
            return

        # LiteLLM packs the per-key metadata into kwargs["litellm_params"]
        # ["metadata"]. The exact subkey naming has drifted across LiteLLM
        # versions; be defensive.
        litellm_params = kwargs.get("litellm_params") or {}
        md = litellm_params.get("metadata") or {}
        key_md = md.get("user_api_key_metadata") or {}
        key_alias = md.get("user_api_key_alias") or ""

        if not _MEM0_KEY or not bool(key_md.get("mem0_enabled")):
            return
        if _managed_by_openwebui(kwargs, litellm_params, md):
            return

        messages = kwargs.get("messages", []) or []
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )

        to_store = []
        if last_user:
            t = _as_text(last_user.get("content", "")).strip()
            if t:
                to_store.append({"role": "user", "content": t})
        if not to_store:
            return

        scope = (
            kwargs.get("user")
            or key_md.get("mem0_scope")
            or key_alias
            or "default"
        )

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SEC * 6) as client:
                await client.post(
                    f"{_MEM0_BASE}/memories",
                    json={"messages": to_store, "user_id": scope, "infer": True,
                          "metadata": {"source": "litellm-callback", "capture": "user-only"}},
                    headers={"X-API-Key": _MEM0_KEY},
                )
        except Exception:
            pass


proxy_handler_instance = Mem0Callback()
