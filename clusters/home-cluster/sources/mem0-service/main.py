"""mem0 memory service: a thin, authenticated REST wrapper over mem0.

Endpoints (all except /health require an API key via either
`Authorization: Bearer <key>` or `X-API-Key: <key>`, checked against
MEM0_API_KEY):

  GET    /health                       liveness/readiness, no auth
  POST   /memories                     add memories from messages/text
  POST   /search                       semantic search within a scope
  GET    /memories?user_id=...         list memories for a scope
  GET    /scopes                       discover populated scopes
  PUT    /memories/{memory_id}         edit one memory
  DELETE /memories/{memory_id}         delete one memory
  GET    /memories/{memory_id}/history view the pod-local audit history
  POST   /memories/delete_many         delete selected memories in one scope
  POST   /memories/delete_duplicates   remove exact duplicate texts in one scope
  POST   /memories/delete_all          safely delete one scope, item by item

The management UI is served from / and uses these same-origin API routes. The
API key is kept in browser session storage, never baked into the image.

Consumers:
  - OpenWebUI (cluster + the shadow12 gaming PC) via the filter function.

Blocking mem0 calls are pushed to a worker thread so they do not stall the
event loop.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import Any

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .memory import get_memory
from .context_store import get_context_checkpoint, put_context_checkpoint

app = FastAPI(title="mem0-service", version="0.5.4", docs_url=None, redoc_url=None)
UI_DIR = Path(__file__).with_name("ui")


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    )
    if request.url.path in {"/", "/index.html"}:
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------- auth ----------
def _expected_key() -> str:
    key = os.environ.get("MEM0_API_KEY")
    if not key:
        # Fail closed: if no key is configured, reject everything rather
        # than silently running an open memory store.
        raise HTTPException(status_code=500, detail="MEM0_API_KEY not configured")
    return key


async def require_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    presented = x_api_key
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if presented != _expected_key():
        raise HTTPException(status_code=401, detail="invalid or missing API key")


# ---------- models ----------
class Message(BaseModel):
    role: str
    content: str


class AddRequest(BaseModel):
    # Provide either messages or text.
    messages: list[Message] | None = None
    text: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] | None = None
    # infer=True lets mem0's LLM extract salient facts. infer=False stores
    # the raw text verbatim (cheaper, no LLM call, but no summarization).
    infer: bool = True


class SearchRequest(BaseModel):
    query: str
    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class DeleteAllRequest(BaseModel):
    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None


class DeleteManyRequest(DeleteAllRequest):
    memory_ids: list[str] = Field(min_length=1, max_length=500)


class UpdateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)


class ContextCheckpointRequest(BaseModel):
    model: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=100_000)
    token_count: int = Field(ge=0, le=1_000_000)


async def _run(func, *args, **kwargs):
    return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))


def _scope_kwargs(user_id, agent_id, run_id) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if user_id:
        kw["user_id"] = user_id
    if agent_id:
        kw["agent_id"] = agent_id
    if run_id:
        kw["run_id"] = run_id
    return kw


# ---------- routes ----------
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/check", dependencies=[Depends(require_key)])
async def auth_check() -> dict[str, bool]:
    return {"ok": True}


def _validate_checkpoint_key(checkpoint_key: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", checkpoint_key):
        raise HTTPException(status_code=422, detail="invalid checkpoint key")


@app.put("/context-checkpoints/{checkpoint_key}", dependencies=[Depends(require_key)])
async def save_context_checkpoint(
    checkpoint_key: str, req: ContextCheckpointRequest
) -> dict[str, Any]:
    _validate_checkpoint_key(checkpoint_key)
    try:
        result = await _run(
            put_context_checkpoint,
            checkpoint_key,
            req.model,
            req.summary,
            req.token_count,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "checkpoint": result}


@app.get("/context-checkpoints/{checkpoint_key}", dependencies=[Depends(require_key)])
async def load_context_checkpoint(checkpoint_key: str) -> dict[str, Any]:
    _validate_checkpoint_key(checkpoint_key)
    try:
        result = await _run(get_context_checkpoint, checkpoint_key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    if result is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    return {"ok": True, "checkpoint": result}


@app.get("/scopes", dependencies=[Depends(require_key)])
async def list_scopes() -> dict[str, Any]:
    try:
        memory = get_memory()
        result = await _run(_discover_scopes, memory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "scopes": result}


def _discover_scopes(memory: Any) -> list[dict[str, Any]]:
    """Return distinct scope IDs without loading vectors or memory text."""
    vector_store = memory.vector_store
    collection = str(vector_store.collection_name)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", collection):
        raise ValueError("invalid Mem0 collection name")
    query = f"""
        WITH scopes AS (
            SELECT 'user_id' AS scope_type, payload->>'user_id' AS scope_value
            FROM {collection}
            UNION ALL
            SELECT 'agent_id', payload->>'agent_id' FROM {collection}
            UNION ALL
            SELECT 'run_id', payload->>'run_id' FROM {collection}
        )
        SELECT scope_type, scope_value, COUNT(*)
        FROM scopes
        WHERE scope_value IS NOT NULL AND scope_value <> ''
        GROUP BY scope_type, scope_value
        ORDER BY scope_type, scope_value
    """
    with vector_store._get_cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
    return [
        {"type": str(scope_type), "value": str(scope_value), "count": int(count)}
        for scope_type, scope_value, count in rows
    ]


@app.post("/memories", dependencies=[Depends(require_key)])
async def add_memories(req: AddRequest) -> dict[str, Any]:
    if not req.messages and not req.text:
        raise HTTPException(status_code=422, detail="provide messages or text")
    if not (req.user_id or req.agent_id or req.run_id):
        raise HTTPException(
            status_code=422,
            detail="provide at least one of user_id, agent_id, run_id to scope the memory",
        )
    payload: Any
    if req.messages:
        payload = [{"role": m.role, "content": m.content} for m in req.messages]
    else:
        payload = req.text

    kwargs = _scope_kwargs(req.user_id, req.agent_id, req.run_id)
    kwargs["infer"] = req.infer
    if req.metadata:
        kwargs["metadata"] = req.metadata

    try:
        memory = get_memory()
        result = await _run(memory.add, payload, **kwargs)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "result": result}


@app.post("/search", dependencies=[Depends(require_key)])
async def search_memories(req: SearchRequest) -> dict[str, Any]:
    if not (req.user_id or req.agent_id or req.run_id):
        raise HTTPException(
            status_code=422,
            detail="provide at least one of user_id, agent_id, run_id to scope the search",
        )
    filters = _scope_kwargs(req.user_id, req.agent_id, req.run_id)
    try:
        memory = get_memory()
        result = await _run(memory.search, req.query, filters=filters, top_k=req.limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "result": result}


@app.get("/memories", dependencies=[Depends(require_key)])
async def list_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict[str, Any]:
    if not (user_id or agent_id or run_id):
        raise HTTPException(
            status_code=422,
            detail="provide at least one of user_id, agent_id, run_id",
        )
    filters = _scope_kwargs(user_id, agent_id, run_id)
    try:
        memory = get_memory()
        result = await _run(memory.get_all, filters=filters, top_k=limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "result": result}


@app.put("/memories/{memory_id}", dependencies=[Depends(require_key)])
async def update_memory(memory_id: str, req: UpdateRequest) -> dict[str, Any]:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="memory text cannot be blank")
    try:
        memory = get_memory()
        result = await _run(_update_preserving_metadata, memory, memory_id, text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "updated": memory_id, "result": result}


def _update_preserving_metadata(memory: Any, memory_id: str, text: str) -> Any:
    """Update through the public SDK while explicitly retaining custom metadata."""
    current = memory.get(memory_id)
    metadata = current.get("metadata") if isinstance(current, dict) else None
    return memory.update(memory_id, text=text, metadata=metadata)


@app.delete("/memories/{memory_id}", dependencies=[Depends(require_key)])
async def delete_memory(memory_id: str) -> dict[str, Any]:
    try:
        memory = get_memory()
        await _run(memory.delete, memory_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "deleted": memory_id}


@app.get("/memories/{memory_id}/history", dependencies=[Depends(require_key)])
async def memory_history(memory_id: str) -> dict[str, Any]:
    try:
        memory = get_memory()
        result = await _run(memory.history, memory_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "result": result}


@app.post("/memories/delete_all", dependencies=[Depends(require_key)])
async def delete_all(req: DeleteAllRequest) -> dict[str, Any]:
    # Guard against nuking the whole store: a scope is mandatory.
    if not (req.user_id or req.agent_id or req.run_id):
        raise HTTPException(
            status_code=422,
            detail="refusing to delete_all without a user_id/agent_id/run_id scope",
        )
    kwargs = _scope_kwargs(req.user_id, req.agent_id, req.run_id)
    deleted_count = 0
    try:
        memory = get_memory()
        # Keep bulk deletion in this wrapper even though mem0ai 2.x no longer
        # resets the collection. Listing and deleting explicit IDs gives this
        # production API a narrow, auditable safety boundary.
        while True:
            listed = await _run(memory.get_all, filters=kwargs, top_k=1000)
            memory_ids = [
                record.get("id")
                for record in _result_records(listed)
                if record.get("id")
            ]
            if not memory_ids:
                break
            for memory_id in memory_ids:
                await _run(memory.delete, memory_id)
                deleted_count += 1
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"ok": True, "scope": kwargs, "deleted_count": deleted_count}


@app.post("/memories/delete_many", dependencies=[Depends(require_key)])
async def delete_many(req: DeleteManyRequest) -> dict[str, Any]:
    """Delete an explicit set only after proving every ID belongs to the scope."""
    if not (req.user_id or req.agent_id or req.run_id):
        raise HTTPException(
            status_code=422,
            detail="refusing to delete selected memories without a user_id/agent_id/run_id scope",
        )
    memory_ids = list(dict.fromkeys(memory_id.strip() for memory_id in req.memory_ids))
    if not all(memory_ids):
        raise HTTPException(status_code=422, detail="memory_ids cannot contain blank values")
    scope = _scope_kwargs(req.user_id, req.agent_id, req.run_id)
    try:
        memory = get_memory()
        deleted_ids = await _run(_delete_many_in_scope, memory, memory_ids, scope)
    except LookupError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {
        "ok": True,
        "scope": scope,
        "deleted_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
    }


@app.post("/memories/delete_duplicates", dependencies=[Depends(require_key)])
async def delete_duplicates(req: DeleteAllRequest) -> dict[str, Any]:
    """Remove only normalized-exact duplicate texts from one explicit scope.

    This intentionally does not attempt semantic deduplication: two similarly
    worded memories can still represent different facts. For each exact text
    group, the most recently updated/created record is kept.
    """
    if not (req.user_id or req.agent_id or req.run_id):
        raise HTTPException(
            status_code=422,
            detail="refusing to remove duplicates without a user_id/agent_id/run_id scope",
        )
    scope = _scope_kwargs(req.user_id, req.agent_id, req.run_id)
    try:
        memory = get_memory()
        deleted_ids, duplicate_groups = await _run(
            _delete_exact_duplicates_in_scope, memory, scope
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {
        "ok": True,
        "scope": scope,
        "deleted_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
        "duplicate_groups": duplicate_groups,
    }


def _delete_many_in_scope(
    memory: Any,
    memory_ids: list[str],
    scope: dict[str, Any],
) -> list[str]:
    """Validate the complete selection before performing the first delete."""
    listed = memory.get_all(filters=scope, top_k=1000)
    in_scope = {
        str(record["id"])
        for record in _result_records(listed)
        if record.get("id")
    }
    missing = [memory_id for memory_id in memory_ids if memory_id not in in_scope]
    if missing:
        raise LookupError(
            "selection is stale or outside the active scope; refresh memories and try again"
        )
    for memory_id in memory_ids:
        memory.delete(memory_id)
    return memory_ids


def _duplicate_key(record: dict[str, Any]) -> str:
    """A conservative key for copies that differ only by case/whitespace."""
    text = record.get("memory") or record.get("text") or ""
    return " ".join(str(text).casefold().split())


def _record_recency(record: dict[str, Any], index: int) -> tuple[str, int]:
    # Mem0 timestamps are ISO-8601, so their string ordering is chronological.
    # The index makes otherwise tied records deterministic.
    return (str(record.get("updated_at") or record.get("created_at") or ""), index)


def _delete_exact_duplicates_in_scope(
    memory: Any, scope: dict[str, Any]
) -> tuple[list[str], int]:
    listed = memory.get_all(filters=scope, top_k=1000)
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(_result_records(listed)):
        if not record.get("id"):
            continue
        key = _duplicate_key(record)
        if key:
            groups.setdefault(key, []).append((index, record))

    deleted_ids: list[str] = []
    duplicate_groups = 0
    for records in groups.values():
        if len(records) < 2:
            continue
        duplicate_groups += 1
        records.sort(key=lambda item: _record_recency(item[1], item[0]), reverse=True)
        for _, record in records[1:]:
            memory.delete(str(record["id"]))
            deleted_ids.append(str(record["id"]))
    return deleted_ids, duplicate_groups


def _result_records(result: Any) -> list[dict[str, Any]]:
    """Normalize mem0 v1.0/v1.1 list shapes."""
    if isinstance(result, dict):
        result = result.get("results", [])
    if not isinstance(result, list):
        return []
    return [item for item in result if isinstance(item, dict)]


# Keep this mount last so it cannot shadow the API routes above.
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="memory-manager-ui")
