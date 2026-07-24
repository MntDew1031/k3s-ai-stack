# Mem0 service and memory console

This build context contains the authenticated FastAPI wrapper around mem0 OSS
and its same-origin management UI. The UI does not add a second datastore or
talk directly to Postgres. Every operation goes through the existing Mem0
instance, so vector embeddings and history behavior stay consistent.

## Console features

- Automatic discovery of populated `user_id`, `agent_id`, and `run_id` scopes,
  with counts, an explicit live refresh, plus browser-local manual shortcuts.
- List up to 1,000 memories in a scope, sort newest/oldest, and filter locally.
- Semantic search through the configured Ollama embedder.
- Add exact text (`infer=false`) or opt into Mem0's LLM extraction.
- Edit text while retaining custom metadata and refreshing the embedding.
- Click anywhere on a memory card to select it (the checkbox still works),
  then delete one, many, or all shown memories after a scope-verified
  confirmation. Exact duplicate cleanup keeps the newest identical copy;
  similar memories are never merged automatically.
- View the pod-local Mem0 history and export the loaded scope as JSON.

The API key is entered at runtime and stored in `sessionStorage`, so it clears
when the browser tab closes. It is never compiled into the UI or persisted in
the image. The service is HTTP on a trusted LAN, so do not expose NodePort
31060 to the public internet.

## Build and local verification

From this directory:

```bash
docker build -t mem0-service:v0.5.4 .
```

The production service remains reachable at
`http://<any-cluster-node-ip>:31060/`. A local container needs the same Mem0,
Postgres, LiteLLM, Ollama, and API-key environment variables defined in
`apps/ai-stack/mem0.yaml`; the UI itself is served by FastAPI at `/`.

Context compaction checkpoints use authenticated
`PUT/GET /context-checkpoints/{sha256-key}` routes. They live in a dedicated
Postgres table, are not vectorized, and never appear as Mem0 memories or GUI
scopes.

Run the source-level tests without connecting to any live dependency:

```bash
python3 -m unittest discover -s tests -v
node --check ui/app.js
```

## Production rollout

Build and push an amd64 image with a new immutable tag, update only the Mem0
image in `apps/ai-stack/mem0.yaml`, then let Flux perform the rolling update.
Do not use `kubectl set image`, and do not reuse an old tag. Verify `/health`, the
existing REST consumers, and the console before considering the rollout done.

This image uses `mem0ai==2.0.12`. Its OSS API requires entity IDs under
`filters` for list/search and uses `top_k`; the wrapper adapts its stable REST
contract to those calls. Mem0 2.x inferred adds use an additive extraction
algorithm, so they no longer rewrite or delete older related memories. Exact
manual edits and deletes still work through this console.

The wrapper deliberately does not delegate scoped bulk deletion to
`Memory.delete_all()`. It lists the selected scope and deletes those IDs
individually so unrelated scopes cannot be erased by an upstream regression.
Selected deletion applies the same boundary: the API verifies every submitted
ID is present in the submitted scope before it performs the first delete.
Duplicate cleanup is equally scoped and only compares normalized exact text
(case and whitespace); it preserves the newest record in each duplicate group.
