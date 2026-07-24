# Local context safety

This stack keeps the user-selected local model for every request. It does not
fall back to a smaller model or a cloud model.

## What users see

- Open WebUI shows a context-usage ring beside Send. Hover for the token
  estimate. Click the ring to prepare `/compact`, then send it. The loader also
  works around v0.10.2's hidden slash-suggestion handler so Enter submits that
  exact command normally. The ring continuously counts the active visible
  conversation, including streamed assistant output, and immediately switches
  to the active post-compaction context without a page reload.
- OpenCode uses its native context ring and native `/compact` command.
- Either client is protected by the LiteLLM pre-call guard when automatic
  compaction is needed.

The displayed percentage uses the safe prompt capacity, so an empty chat starts
near zero and the fixed answer reserve does not create a 13% floor. Automatic
compaction still reserves 4,096 tokens for the answer and fires when the ring
reaches 99%.

## Shared LiteLLM guard

The callback is `litellm-context-callback.py`. Its model limit map and tuning
are set in `litellm.yaml`:

```text
CONTEXT_COMPACTION_THRESHOLD=0.99
CONTEXT_COMPACTION_OUTPUT_RESERVE=4096
CONTEXT_COMPACTION_KEEP_RECENT=8192
CONTEXT_COMPACTION_TIMEOUT_S=90
CONTEXT_COMPACTION_DEFAULT_LIMIT=32768
CONTEXT_COMPACTION_LIMITS_JSON={"SP-qwen3.6:35b":32768,"primary-agent-llm":32768}
```

Every present and future alias is covered. Add a verified limit to model
metadata or the JSON override map when it differs from the conservative 32K
default.

The manual command is intercepted before the normal answer call. The same
selected model creates the checkpoint for every alias, including ordinary
non-`SP-*` aliases and future models. LiteLLM then returns a deterministic
one-line `[Context compacted]` acknowledgement. The checkpoint payload is
stored in a dedicated Postgres table through the local Mem0 service; it is not
printed into chat, vectorized, or exposed as a memory scope.
Later requests keep system instructions and dialogue after the latest
checkpoint while dropping older dialogue. Automatic compaction is request-local,
so it protects clients that do not persist an explicit checkpoint.

## Open WebUI usage accuracy

The ring estimates conservatively before dispatch and updates from the live
conversation while an answer streams. It displays growth since the latest
manual checkpoint and returns to 0 immediately after `/compact`; the server
guard separately accounts for checkpoint tokens when enforcing the real hard
limit. Completion telemetry cannot overwrite the live state with stale usage.

The loader is a same-origin browser script. It observes Open WebUI's existing
requests and responses only. It does not transmit chat content or credentials
to another endpoint.

## Verification

```sh
python3 -m unittest discover -s apps/ai-stack/tests -v
node --check apps/ai-stack/openwebui-context-meter.js
kubectl kustomize apps/ai-stack >/tmp/ai-stack-rendered.yaml
```

After deployment, verify that LiteLLM is ready before testing a long prompt.
The Deployment uses `maxUnavailable: 0`, `maxSurge: 1`, and ten seconds of
minimum readiness. Open WebUI uses `OnDelete`, so an application upgrade needs
a deliberate pod recreation. The ConfigMap is mounted directly over v0.10.2's
served `/app/backend/open_webui/static/loader.js` because the hardened uid 1000
process cannot overwrite the image's root-owned static directory.
