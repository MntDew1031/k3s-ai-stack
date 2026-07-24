/* Open WebUI v0.10.2 context meter.
 *
 * Loaded through the existing /static/loader.js hook. It observes same-origin
 * chat completion traffic, reads token usage already returned by Open WebUI,
 * and renders a small ring inside the composer. No chat text or credential
 * leaves the browser.
 */
(function contextMeter() {
  "use strict";

  const FALLBACK_LIMITS = {
    "SP-qwen3.6:35b": 32768,
    "primary-agent-llm": 32768,
  };
  const DEFAULT_LIMIT = 32768;
  const COMPACTION_THRESHOLD = 0.99;
  const RESPONSE_RESERVE = 4096;
  const limits = new Map(Object.entries(FALLBACK_LIMITS));
  const state = {
    model: sessionStorage.getItem("context-meter-last-model") || "active-model",
    promptTokens: 0,
    displayTokens: 0,
    systemTokens: 0,
    percentage: 0,
    checkpointText: "",
    checkpointTokens: 0,
    requestPromptTokens: 0,
    domSignature: "",
  };
  const originalFetch = window.fetch.bind(window);

  function estimateTokens(value) {
    const text = typeof value === "string" ? value : JSON.stringify(value || "");
    return Math.max(1, Math.ceil(text.length / 3));
  }

  function modelLimit(model) {
    return Number(limits.get(model)) || DEFAULT_LIMIT;
  }

  function safePromptLimit(model) {
    return Math.max(1, Math.floor(modelLimit(model) * COMPACTION_THRESHOLD) - RESPONSE_RESERVE);
  }

  function update(model, promptTokens, displayTokens = promptTokens) {
    state.model = model || state.model || "active-model";
    if (state.model) sessionStorage.setItem("context-meter-last-model", state.model);
    state.promptTokens = Math.max(0, Number(promptTokens) || 0);
    state.displayTokens = Math.max(0, Number(displayTokens) || 0);
    const percentage = Math.min(
      100,
      Math.max(0, Math.round((state.displayTokens / safePromptLimit(state.model)) * 99)),
    );
    state.percentage = state.displayTokens > 0 ? Math.max(1, percentage) : 0;
    render();
  }

  function messageContent(message) {
    if (!message) return "";
    return typeof message.content === "string"
      ? message.content
      : JSON.stringify(message.content || "");
  }

  function activeMessages(messages) {
    if (!Array.isArray(messages)) return [];
    let checkpointIndex = -1;
    for (let index = 0; index < messages.length; index += 1) {
      const message = messages[index];
      if (message && message.role === "assistant" && messageContent(message).includes("[Context compacted]")) {
        checkpointIndex = index;
      }
    }
    if (checkpointIndex < 0) return messages;
    const systems = messages
      .slice(0, checkpointIndex)
      .filter((message) => message && message.role === "system");
    const checkpoint = messageContent(messages[checkpointIndex]).trim();
    const tail = messages.slice(checkpointIndex + 1).filter((message) => {
      return !(message && message.role === "user" && messageContent(message).trim().toLowerCase() === "/compact");
    });
    return systems.concat([
      { role: "system", content: `Conversation continuation checkpoint:\n${checkpoint}` },
    ], tail);
  }

  function isCompactRequest(messages) {
    if (!Array.isArray(messages)) return false;
    const lastUser = [...messages].reverse().find((message) => message && message.role === "user");
    return Boolean(lastUser && messageContent(lastUser).trim().toLowerCase() === "/compact");
  }

  function color(percentage) {
    if (percentage >= 99) return "#ef4444";
    if (percentage >= 90) return "#f59e0b";
    return "#22c55e";
  }

  function ensureStyles() {
    if (document.getElementById("context-meter-styles")) return;
    const style = document.createElement("style");
    style.id = "context-meter-styles";
    style.textContent = `
      #context-usage-ring {
        position: fixed; z-index: 9999;
        display: inline-grid; place-items: center;
        width: 32px; height: 32px; flex: 0 0 auto; border: 0;
        border-radius: 9999px; color: currentColor; background: transparent;
        cursor: pointer;
      }
      #context-usage-ring:hover { background: rgba(127,127,127,.12); }
      #context-usage-ring svg { width: 22px; height: 22px; transform: rotate(-90deg); }
      #context-usage-ring circle { fill: none; stroke-width: 2.5; }
      #context-usage-ring .context-track { stroke: rgba(127,127,127,.25); }
      #context-usage-ring .context-value {
        stroke-linecap: round; transition: stroke-dashoffset .2s ease, stroke .2s ease;
      }
      #context-usage-ring span {
        position: absolute; font-size: 7px; line-height: 1; font-weight: 700;
      }
    `;
    document.head.append(style);
  }

  function ringElement() {
    const button = document.createElement("button");
    button.id = "context-usage-ring";
    button.type = "button";
    button.setAttribute("aria-label", "Context window usage. Click to prepare /compact");
    button.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle class="context-track" cx="12" cy="12" r="9"></circle>
        <circle class="context-value" cx="12" cy="12" r="9"></circle>
      </svg>
      <span>0</span>
    `;
    button.addEventListener("click", () => {
      const input = document.getElementById("chat-input");
      if (!input) return;
      if (input instanceof window.HTMLTextAreaElement) {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype,
          "value",
        )?.set;
        if (setter) setter.call(input, "/compact");
        else input.value = "/compact";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
        return;
      }

      // v0.10 uses a ProseMirror contenteditable input. execCommand emits the
      // editing events that ProseMirror consumes, unlike assigning textContent.
      input.focus();
      document.execCommand("selectAll", false);
      document.execCommand("insertText", false, "/compact");
    });
    return button;
  }

  function mount() {
    ensureStyles();
    if (!document.body) return;
    if (!document.getElementById("context-usage-ring")) {
      // Keep the control outside Svelte's managed composer subtree. Svelte
      // removes unknown children whenever prompt state causes that subtree to
      // reconcile, which made earlier composer-mounted versions disappear.
      document.body.appendChild(ringElement());
    }
    positionRing();
  }

  function positionRing() {
    const ring = document.getElementById("context-usage-ring");
    const composer = document.getElementById("message-input-container");
    if (!ring) return;
    if (!composer) {
      ring.style.display = "none";
      return;
    }
    const rect = composer.getBoundingClientRect();
    ring.style.display = "inline-grid";
    ring.style.left = `${Math.max(8, Math.min(window.innerWidth - 40, rect.right - 136))}px`;
    ring.style.top = `${Math.max(8, Math.min(window.innerHeight - 40, rect.bottom - 43))}px`;
  }

  function compactPromptText(input) {
    const value = input instanceof window.HTMLTextAreaElement
      ? input.value
      : input.innerText || input.textContent || "";
    return value.replace(/\u00a0/g, " ").trim();
  }

  function submitCompactOnEnter(event) {
    if (event.key !== "Enter" || event.shiftKey || event.altKey) return;
    const input = document.getElementById("chat-input");
    if (!input || (document.activeElement !== input && !input.contains(document.activeElement))) return;
    if (compactPromptText(input) !== "/compact") return;
    const send = document.getElementById("send-message-button");
    if (!send || send.disabled) return;

    // Open WebUI v0.10.2 keeps a hidden slash-suggestion element mounted.
    // Its key handler consumes Enter before the normal submit branch. Capture
    // this one exact local command and use the application's own send button.
    event.preventDefault();
    event.stopImmediatePropagation();
    send.click();
  }

  function flashCompacted() {
    const ring = document.getElementById("context-usage-ring");
    const label = ring && ring.querySelector("span");
    if (!label) return;
    label.textContent = "✓";
    window.setTimeout(render, 1200);
  }

  function applyCheckpoint(checkpointText) {
    const text = String(checkpointText || "").trim();
    if (!text.includes("[Context compacted]") || text === state.checkpointText) return false;
    const firstCheckpoint = !state.checkpointText;
    state.checkpointText = text;
    const tokenMatch = text.match(/\[Context compacted\]\s*\(([\d,]+)\s+tokens\)/i);
    state.checkpointTokens = tokenMatch
      ? Number(tokenMatch[1].replace(/,/g, ""))
      : estimateTokens(text);
    // The ring visualizes growth since the latest checkpoint. The server-side
    // guard still accounts for the checkpoint itself when enforcing limits.
    update(state.model, state.systemTokens + state.checkpointTokens, 0);
    if (firstCheckpoint) flashCompacted();
    return true;
  }

  function scanConversationFromDom() {
    const nodes = [...document.querySelectorAll(".chat-user, .chat-assistant")];
    if (!nodes.length) return;
    // Clean up checkpoints produced by the retired HTML-comment experiment.
    // This changes presentation only; the original raw message remains in
    // Open WebUI history so LiteLLM can decode and migrate it on the next turn.
    for (const node of nodes) {
      const text = (node.innerText || node.textContent || "").replace(/\u00a0/g, " ").trim();
      if (!text.includes("<!--context-checkpoint-b64:")) continue;
      const marker = text.match(/\[Context compacted\]\s*\([\d,]+\s+tokens\)/i)?.[0];
      if (marker && node.textContent !== marker) node.textContent = marker;
    }
    const entries = nodes.map((node) => ({
      role: node.classList.contains("chat-user") ? "user" : "assistant",
      text: (node.innerText || node.textContent || "").replace(/\u00a0/g, " ").trim(),
    }));
    let checkpointIndex = -1;
    for (let index = 0; index < entries.length; index += 1) {
      if (entries[index].role === "assistant" && entries[index].text.includes("[Context compacted]")) {
        checkpointIndex = index;
      }
    }

    let total = state.systemTokens;
    let baseline = 0;
    let start = 0;
    if (checkpointIndex >= 0) {
      const checkpoint = entries[checkpointIndex].text;
      const tokenMatch = checkpoint.match(/\[Context compacted\]\s*\(([\d,]+)\s+tokens\)/i);
      const persistedTokens = tokenMatch
        ? Number(tokenMatch[1].replace(/,/g, ""))
        : state.checkpointTokens || estimateTokens(checkpoint);
      state.checkpointTokens = persistedTokens;
      state.checkpointText = checkpoint;
      total += persistedTokens;
      baseline = total;
      start = checkpointIndex + 1;
    }
    const active = entries.slice(start).filter((entry) => {
      return entry.text && !(entry.role === "user" && entry.text.toLowerCase() === "/compact");
    });
    for (const entry of active) total += 5 + estimateTokens(entry.text);
    const displayTokens = Math.max(0, total - baseline);
    const signature = `${state.model}:${checkpointIndex}:${total}:${displayTokens}:${active.map((entry) => entry.text).join("\u241e")}`;
    if (signature === state.domSignature) return;
    state.domSignature = signature;
    update(state.model, total, displayTokens);
  }

  function render() {
    mount();
    const ring = document.getElementById("context-usage-ring");
    if (!ring) return;
    const value = ring.querySelector(".context-value");
    const label = ring.querySelector("span");
    const circumference = 2 * Math.PI * 9;
    value.style.strokeDasharray = String(circumference);
    value.style.strokeDashoffset = String(circumference * (1 - state.percentage / 100));
    value.style.stroke = color(state.percentage);
    label.textContent = String(state.percentage);
    const limit = modelLimit(state.model);
    const promptLimit = safePromptLimit(state.model);
    ring.title = state.model
      ? `${state.percentage}% context growth since the latest compact for ${state.model}\n${state.displayTokens.toLocaleString()} active conversation tokens since compact\n${state.promptTokens.toLocaleString()} estimated total prompt tokens; automatic compaction is enforced server-side before ${promptLimit.toLocaleString()}.\n${RESPONSE_RESERVE.toLocaleString()} tokens remain reserved for the answer; full context is ${limit.toLocaleString()}.`
      : "Context usage appears after the first request. Click to prepare /compact.";
  }

  function ingestModels(payload) {
    const models = Array.isArray(payload) ? payload : payload && (payload.data || payload.models);
    if (!Array.isArray(models)) return;
    for (const model of models) {
      const id = model && (model.id || model.model);
      const info = (model && model.info) || {};
      const meta = info.meta || {};
      const limit =
        meta.max_input_tokens ||
        info.max_input_tokens ||
        meta.context_length ||
        info.context_length ||
        model.context_length;
      if (id && Number(limit) > 0) limits.set(id, Number(limit));
    }
    if (state.model) update(state.model, state.promptTokens, state.displayTokens);
  }

  function responseContent(text) {
    const chunks = [];
    const addPayload = (payload) => {
      const choices = payload && payload.choices;
      if (Array.isArray(choices)) {
        for (const choice of choices) {
          const content = choice?.delta?.content ?? choice?.message?.content ?? choice?.text;
          if (typeof content === "string") chunks.push(content);
        }
      }
      const direct = payload?.message?.content;
      if (typeof direct === "string") chunks.push(direct);
      const eventContent = payload?.data?.content;
      if (typeof eventContent === "string") chunks.push(eventContent);
    };
    try {
      addPayload(JSON.parse(text));
    } catch (_) {
      for (const line of text.split(/\r?\n/)) {
        const raw = line.startsWith("data:") ? line.slice(5).trim() : "";
        if (!raw || raw === "[DONE]") continue;
        try {
          addPayload(JSON.parse(raw));
        } catch (_) {
          // Ignore non-JSON status events.
        }
      }
    }
    return chunks.join("");
  }

  async function inspectResponse(response, model, isModels, compactRequest) {
    try {
      const text = await response.text();
      if (isModels) {
        ingestModels(JSON.parse(text));
        return;
      }
      const content = responseContent(text);
      if (compactRequest) {
        applyCheckpoint(content);
        return;
      }
      // The DOM is authoritative for normal replies. Usage returned after a
      // completion describes the just-finished request and previously restored
      // stale pre-compaction values (the observed 13% floor).
    } catch (_) {
      // Telemetry must never interfere with chat.
    }
  }

  window.fetch = async function contextAwareFetch(input, init) {
    const url = typeof input === "string" ? input : input && input.url ? input.url : "";
    const isChat = /\/api\/chat\/completions(?:\?|$)/.test(url);
    const isModels = /\/api\/models(?:\?|$)/.test(url);
    let model = state.model;
    let compactRequest = false;

    if (isChat && init && typeof init.body === "string") {
      try {
        const body = JSON.parse(init.body);
        model = body.model || model;
        const messages = activeMessages(body.messages || []);
        compactRequest = isCompactRequest(body.messages || []);
        state.systemTokens = estimateTokens(
          messages.filter((message) => message && message.role === "system"),
        );
        if (compactRequest) state.checkpointText = "";
        const rawMessages = body.messages || [];
        let lastCheckpoint = -1;
        for (let index = 0; index < rawMessages.length; index += 1) {
          if (
            rawMessages[index]?.role === "assistant" &&
            messageContent(rawMessages[index]).includes("[Context compacted]")
          ) lastCheckpoint = index;
        }
        const tail = lastCheckpoint >= 0
          ? rawMessages.slice(lastCheckpoint + 1).filter((message) => {
              return !(message?.role === "user" && messageContent(message).trim().toLowerCase() === "/compact");
            })
          : rawMessages;
        const toolTokens = estimateTokens(body.tools || []);
        const promptTokens = estimateTokens(messages) + toolTokens + (lastCheckpoint >= 0 ? state.checkpointTokens : 0);
        const displayTokens = lastCheckpoint >= 0 ? estimateTokens(tail) + toolTokens : promptTokens;
        state.requestPromptTokens = promptTokens;
        update(model, promptTokens, compactRequest ? state.displayTokens : displayTokens);
      } catch (_) {
        // Keep the last known usage if the request body is not JSON.
      }
    }

    const response = await originalFetch(input, init);
    if ((isChat || isModels) && response && response.ok) {
      void inspectResponse(response.clone(), model, isModels, compactRequest);
    }
    return response;
  };

  let scanScheduled = false;
  const scheduleScan = () => {
    if (scanScheduled) return;
    scanScheduled = true;
    requestAnimationFrame(() => {
      scanScheduled = false;
      mount();
      scanConversationFromDom();
    });
  };
  const observer = new MutationObserver(scheduleScan);
  const start = () => {
    observer.observe(document.documentElement, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    document.addEventListener("keydown", submitCompactOnEnter, true);
    window.addEventListener("resize", positionRing);
    window.addEventListener("scroll", positionRing, true);
    mount();
    scanConversationFromDom();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
