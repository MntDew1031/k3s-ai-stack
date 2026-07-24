"""mem0 Memory factory.

Builds a single mem0 Memory instance wired to:
  - vectors:  pgvector (the mem0-db Postgres in ai-stack)
  - LLM:      whatever MEM0_LLM_MODEL names, routed through the LiteLLM
              gateway (openai-compatible). Because it goes through LiteLLM,
              the model can be an API model (gpt-5-mini, claude-haiku-4.5)
              OR a local ollama model (qwen3.5:9b, llama3.2:latest) with no
              code change, just a different MEM0_LLM_MODEL value.
  - embedder: ollama nomic-embed-text, served by the in-cluster ollama pod.
              LiteLLM does not proxy an embeddings model, so embeddings go
              straight to ollama. nomic-embed-text outputs 768-dim vectors,
              so MEM0_EMBED_DIMS MUST stay 768 and match the pgvector
              embedding_model_dims, or inserts fail with
              "shapes (0,1536) and (768,) not aligned".

Config is built from env vars so the whole thing is declarative via Flux.
The Memory instance is created lazily on first use so the pod can pass its
/health probe even while ollama is still pulling the embed model.
"""
from __future__ import annotations

import os
import threading

from mem0 import Memory

_memory: Memory | None = None
_lock = threading.Lock()


# Stricter fact-extraction prompt than mem0's default. The default prompt is
# tuned for casual chat and extracts a lot of conversational/meta noise (the
# assistant's own actions, planning intents, ephemeral tool outputs). Coding
# agents (OpenCode, etc.) generate a huge amount of that noise, so we lock
# the extractor down to durable, reusable facts only.
#
# Override at deploy time by setting MEM0_CUSTOM_EXTRACTION_PROMPT to a
# non-empty string. Set it to a single literal "default" to fall back to
# mem0's built-in prompt.
DEFAULT_EXTRACTION_PROMPT = """Extract only DURABLE, REUSABLE facts about the user and their projects from a conversation.

EXTRACT:
- User preferences and decisions (e.g. "the operator prefers sops with age over kubectl secrets")
- Project facts with specific values (e.g. "The k3s_homelab repo uses Flux GitOps with kustomize overlays")
- Stable identifiers, paths, model names, hostnames, IP addresses, ports
- Personal info the user explicitly shares (location, role, hardware, tools used)
- Lessons learned, resolved technical knowledge, configuration that worked

DO NOT EXTRACT:
- The assistant's own actions ("Assistant wrote a file", "Assistant searched the codebase")
- Planning, intent, or search statements ("Looking for X", "Will check Y", "Wants to find Z")
- Tool call descriptions, function arguments, or placeholder paths such as /path/to/... or /absolute/path/to/...
- Restatements of content the user just showed in the conversation
- Vague observations ("Found a directory that looks promising", "This appears to be a Pod definition")
- Transient debugging steps, exploration breadcrumbs, or status updates

Each extracted memory must be a complete declarative sentence about the USER or their PROJECTS that would still be useful and accurate one week from now in a totally different conversation. If a fact does not pass that test, leave it out. Follow Mem0's required response schema exactly.
"""


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"required env var {name} is not set")
    return val  # type: ignore[return-value]


def build_config() -> dict:
    embed_dims = int(_env("MEM0_EMBED_DIMS", "768"))
    config = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "host": _env("MEM0_PG_HOST", required=True),
                "port": int(_env("MEM0_PG_PORT", "5432")),
                "dbname": _env("MEM0_PG_DB", "mem0"),
                "user": _env("MEM0_PG_USER", required=True),
                "password": _env("MEM0_PG_PASSWORD", required=True),
                "collection_name": _env("MEM0_COLLECTION", "mem0_memories"),
                "embedding_model_dims": embed_dims,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": _env("MEM0_LLM_MODEL", "gpt-5-mini"),
                "openai_base_url": _env("MEM0_LLM_BASE_URL", required=True),
                "api_key": _env("MEM0_LLM_API_KEY", required=True),
                "temperature": float(_env("MEM0_LLM_TEMPERATURE", "0.1")),
                "max_tokens": int(_env("MEM0_LLM_MAX_TOKENS", "2000")),
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": _env("MEM0_EMBED_MODEL", "nomic-embed-text"),
                "ollama_base_url": _env("MEM0_OLLAMA_BASE_URL", required=True),
                "embedding_dims": embed_dims,
            },
        },
        # History (audit log of add/update/delete) on a writable mount.
        # Lives on an emptyDir, so it resets on pod restart. The real
        # memories live in pgvector and survive restarts; history is only
        # an audit trail, so this is an acceptable trade.
        "history_db_path": _env("MEM0_HISTORY_DB", "/data/history.db"),
    }

    # Wire in the custom extraction prompt unless the operator has set
    # MEM0_CUSTOM_EXTRACTION_PROMPT to the literal string "default", in
    # which case fall back to mem0's built-in prompt.
    prompt_override = os.environ.get("MEM0_CUSTOM_EXTRACTION_PROMPT", "").strip()
    if prompt_override.lower() == "default":
        pass  # use mem0's built-in
    elif prompt_override:
        config["custom_instructions"] = prompt_override
    else:
        config["custom_instructions"] = DEFAULT_EXTRACTION_PROMPT

    return config


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        with _lock:
            if _memory is None:
                _memory = Memory.from_config(build_config())
    return _memory
