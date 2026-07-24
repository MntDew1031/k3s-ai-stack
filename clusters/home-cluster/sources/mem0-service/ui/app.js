"use strict";

const byId = (id) => document.getElementById(id);
const state = {
  apiKey: sessionStorage.getItem("mem0-api-key") || "",
  scopes: loadScopes(),
  activeScope: null,
  memories: [],
  view: "browse",
  sortOrder: localStorage.getItem("mem0-sort-order") === "oldest" ? "oldest" : "newest",
  selectedIds: new Set(),
  pendingConfirm: null,
};

const ui = {
  scopeList: byId("scope-list"), noScopes: byId("no-scopes"), scopeTitle: byId("scope-title"),
  scopeKindLabel: byId("scope-kind-label"), memoryCount: byId("memory-count"), scopeStat: byId("scope-stat"),
  modeStat: byId("mode-stat"), grid: byId("memory-grid"), empty: byId("empty-state"), loading: byId("loading"),
  notice: byId("notice"), resultsHeading: byId("results-heading"), resultsSubtitle: byId("results-subtitle"),
  filterInput: byId("filter-input"), searchInput: byId("search-input"), dangerZone: byId("danger-zone"),
  dangerScope: byId("danger-scope"), statusDot: byId("status-dot"), connectionLabel: byId("connection-label"),
  sortOrder: byId("sort-order"), selectionBar: byId("selection-bar"), selectVisible: byId("select-visible"),
  selectedCount: byId("selected-count"), deleteSelected: byId("delete-selected"),
  removeDuplicates: byId("remove-duplicates"),
};

function loadScopes() {
  try {
    const parsed = JSON.parse(localStorage.getItem("mem0-scopes") || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((s) => s && s.type && s.value).map((s) => ({ ...s, saved: true }))
      : [];
  } catch (_) {
    return [];
  }
}

function saveScopes() {
  const saved = state.scopes.filter((scope) => scope.saved).map(({ type, value }) => ({ type, value }));
  localStorage.setItem("mem0-scopes", JSON.stringify(saved));
}

function typeLabel(type) {
  return { user_id: "User ID", agent_id: "Agent ID", run_id: "Run ID" }[type] || type;
}

function scopePayload(scope = state.activeScope) {
  return scope ? { [scope.type]: scope.value } : {};
}

function scopeKey(scope) {
  return `${scope.type}:${scope.value}`;
}

async function discoverScopes({ notify = false } = {}) {
  const payload = await api("/scopes");
  const discovered = Array.isArray(payload.scopes) ? payload.scopes : [];
  const activeKey = state.activeScope ? scopeKey(state.activeScope) : null;
  const merged = state.scopes
    .filter((scope) => scope.saved)
    .map((scope) => ({ type: scope.type, value: scope.value, saved: true }));

  for (const incoming of discovered) {
    if (!incoming || !incoming.type || !incoming.value) continue;
    const existing = merged.find((item) => scopeKey(item) === scopeKey(incoming));
    if (existing) existing.count = incoming.count;
    else merged.push({ type: incoming.type, value: incoming.value, count: incoming.count, saved: false });
  }

  merged.sort((left, right) => scopeKey(left).localeCompare(scopeKey(right)));
  state.scopes = merged;
  if (activeKey) {
    state.activeScope = state.scopes.find((scope) => scopeKey(scope) === activeKey) || null;
    if (!state.activeScope) clearActiveScope();
  }
  renderScopes();
  if (notify) toast(`Scopes refreshed from Mem0 (${discovered.length} found)`);
}

function extractRecords(payload) {
  let value = payload && Object.prototype.hasOwnProperty.call(payload, "result") ? payload.result : payload;
  if (value && !Array.isArray(value) && Array.isArray(value.results)) value = value.results;
  return Array.isArray(value) ? value : [];
}

async function api(path, options = {}) {
  if (!state.apiKey) throw new Error("Enter the Mem0 API key first.");
  const headers = new Headers(options.headers || {});
  headers.set("X-API-Key", state.apiKey);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = {}; }
  if (!response.ok) {
    const detail = payload && payload.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
  }
  return payload;
}

function setConnected(connected) {
  ui.statusDot.classList.toggle("online", connected);
  ui.connectionLabel.textContent = connected ? "Connected" : "API key required";
}

function setBusy(busy) {
  ui.loading.hidden = !busy;
  if (busy) {
    ui.selectionBar.hidden = true;
    ui.grid.replaceChildren();
    ui.empty.hidden = true;
    ui.notice.hidden = true;
  }
}

function showNotice(message) {
  ui.notice.textContent = message;
  ui.notice.hidden = false;
}

function toast(message, kind = "success") {
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  byId("toast-region").append(item);
  setTimeout(() => item.remove(), 3600);
}

function updateControls() {
  const enabled = Boolean(state.activeScope && state.apiKey);
  ["new-memory-button", "export-button", "search-input", "search-button", "show-all-button", "refresh-button", "filter-input", "sort-order", "delete-scope-button"]
    .forEach((id) => { byId(id).disabled = !enabled; });
  byId("refresh-scopes-button").disabled = !state.apiKey;
  ui.dangerZone.hidden = !state.activeScope;
}

function renderScopes() {
  ui.scopeList.replaceChildren();
  ui.noScopes.hidden = state.scopes.length > 0;
  for (const scope of state.scopes) {
    const row = document.createElement("div");
    row.className = "scope-item" + (state.activeScope === scope ? " active" : "");
    const select = document.createElement("button");
    select.className = "scope-select";
    select.type = "button";
    select.setAttribute("aria-label", `Open ${typeLabel(scope.type)} ${scope.value}`);

    const glyph = document.createElement("span");
    glyph.className = "scope-glyph";
    glyph.textContent = scope.type === "user_id" ? "U" : scope.type === "agent_id" ? "A" : "R";
    const name = document.createElement("span");
    name.className = "scope-name";
    name.textContent = scope.value;
    const count = document.createElement("span");
    count.className = "scope-count";
    count.textContent = Number.isInteger(scope.count) ? String(scope.count) : "";
    const remove = document.createElement("button");
    remove.className = "scope-remove";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove scope ${scope.value} from this list`);
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      state.scopes = state.scopes.filter((item) => item !== scope);
      if (state.activeScope === scope) clearActiveScope();
      saveScopes();
      renderScopes();
    });
    select.addEventListener("click", () => selectScope(scope));
    select.append(glyph, name, count);
    row.append(select, remove);
    ui.scopeList.append(row);
  }
}

function clearActiveScope() {
  state.activeScope = null;
  state.memories = [];
  state.selectedIds.clear();
  ui.scopeKindLabel.textContent = "No scope selected";
  ui.scopeTitle.textContent = "Choose a memory scope";
  ui.scopeStat.textContent = "None";
  ui.memoryCount.textContent = "0";
  ui.dangerScope.textContent = "";
  ui.resultsSubtitle.textContent = "Select a scope to load its memories.";
  renderMemories();
  updateControls();
}

async function selectScope(scope) {
  localStorage.setItem("mem0-active-scope", `${scope.type}:${scope.value}`);
  state.activeScope = scope;
  state.memories = [];
  state.selectedIds.clear();
  state.view = "browse";
  ui.scopeKindLabel.textContent = typeLabel(scope.type);
  ui.scopeTitle.textContent = scope.value;
  ui.scopeStat.textContent = scope.value;
  ui.dangerScope.textContent = scope.value;
  ui.searchInput.value = "";
  ui.filterInput.value = "";
  renderScopes();
  updateControls();
  renderMemories();
  if (!state.apiKey) {
    byId("connection-dialog").showModal();
    return;
  }
  await loadMemories();
}

async function loadMemories() {
  if (!state.activeScope) return;
  setBusy(true);
  state.view = "browse";
  try {
    const params = new URLSearchParams({ ...scopePayload(), limit: "1000" });
    const payload = await api(`/memories?${params}`);
    state.memories = extractRecords(payload);
    state.selectedIds.clear();
    setConnected(true);
    renderMemories();
  } catch (error) {
    state.memories = [];
    if (/API key|invalid|missing/i.test(error.message)) setConnected(false);
    renderMemories();
    showNotice(error.message);
  } finally {
    setBusy(false);
  }
}

async function searchMemories(query) {
  setBusy(true);
  state.view = "search";
  try {
    const payload = await api("/search", {
      method: "POST",
      body: JSON.stringify({ query, ...scopePayload(), limit: 100 }),
    });
    state.memories = extractRecords(payload);
    state.selectedIds.clear();
    renderMemories();
  } catch (error) {
    state.memories = [];
    renderMemories();
    showNotice(error.message);
  } finally {
    setBusy(false);
  }
}

function visibleMemories() {
  const query = ui.filterInput.value.trim().toLowerCase();
  const filtered = query
    ? state.memories.filter((item) => JSON.stringify(item).toLowerCase().includes(query))
    : state.memories;
  return sortMemories(filtered, state.sortOrder);
}

function renderMemories() {
  const query = ui.filterInput.value.trim().toLowerCase();
  const records = visibleMemories();

  ui.grid.replaceChildren();
  ui.notice.hidden = true;
  ui.memoryCount.textContent = String(state.memories.length);
  ui.modeStat.textContent = state.view === "search" ? "Semantic search" : "Browse";
  ui.resultsHeading.textContent = state.view === "search" ? "Search results" : "Memories";
  ui.resultsSubtitle.textContent = state.activeScope
    ? `${records.length} shown in ${state.activeScope.value}`
    : "Select a scope to load its memories.";
  updateSelectionControls(records);

  if (!state.activeScope || records.length === 0) {
    ui.empty.hidden = false;
    const title = ui.empty.querySelector("h3");
    const copy = ui.empty.querySelector("p");
    if (!state.activeScope) {
      title.textContent = "Your memory workspace is ready";
      copy.textContent = "Connect with the Mem0 API key, then add or select a scope.";
    } else if (!state.apiKey) {
      title.textContent = "Connect to load this scope";
      copy.textContent = "Enter the Mem0 API key to list, search, add, edit, or delete memories.";
    } else if (state.view === "search") {
      title.textContent = "No semantic matches";
      copy.textContent = "Try a broader idea, or return to all memories in this scope.";
    } else if (query) {
      title.textContent = "No filtered matches";
      copy.textContent = "Clear the local filter to see every loaded memory.";
    } else {
      title.textContent = "This scope is empty";
      copy.textContent = "Add a durable fact manually or enable AI extraction when creating a memory.";
    }
    return;
  }

  ui.empty.hidden = true;
  for (const memory of records) ui.grid.append(createMemoryCard(memory));
}

function sortMemories(records, order) {
  return records
    .map((record, index) => ({ record, index, timestamp: memoryTimestamp(record) }))
    .sort((left, right) => {
      if (left.timestamp === null && right.timestamp === null) return left.index - right.index;
      if (left.timestamp === null) return 1;
      if (right.timestamp === null) return -1;
      const difference = order === "oldest"
        ? left.timestamp - right.timestamp
        : right.timestamp - left.timestamp;
      return difference || left.index - right.index;
    })
    .map(({ record }) => record);
}

function memoryTimestamp(memory) {
  const value = memory.updated_at || memory.created_at;
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
}

function createMemoryCard(memory) {
  const card = document.createElement("article");
  const initiallySelected = state.selectedIds.has(memory.id);
  card.className = "memory-card" + (initiallySelected ? " selected" : "");
  card.tabIndex = 0;
  card.setAttribute("role", "checkbox");
  card.setAttribute("aria-label", `Select memory ${memory.id || "with unknown ID"}`);
  card.setAttribute("aria-checked", String(initiallySelected));
  const top = document.createElement("div");
  top.className = "card-top";
  const selection = document.createElement("label");
  selection.className = "memory-select";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = initiallySelected;
  checkbox.setAttribute("aria-label", `Select memory ${memory.id || "with unknown ID"}`);
  checkbox.addEventListener("change", () => {
    setMemorySelected(memory.id, card, checkbox, checkbox.checked);
  });
  selection.append(checkbox);
  const id = document.createElement("span");
  id.className = "memory-id";
  id.title = memory.id || "";
  id.textContent = memory.id || "Unknown ID";
  top.append(selection, id);
  if (typeof memory.score === "number") {
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = `${Math.round(memory.score * 100)}% match`;
    top.append(score);
  }

  const text = document.createElement("p");
  text.className = "memory-text";
  text.textContent = memory.memory || memory.text || "";
  card.append(top, text);

  const metadata = memory.metadata && typeof memory.metadata === "object" ? memory.metadata : {};
  const metadataEntries = Object.entries(metadata).slice(0, 4);
  if (metadataEntries.length) {
    const row = document.createElement("div");
    row.className = "metadata-row";
    for (const [key, value] of metadataEntries) {
      const chip = document.createElement("span");
      chip.className = "metadata-chip";
      chip.textContent = `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`;
      row.append(chip);
    }
    card.append(row);
  }

  const bottom = document.createElement("div");
  bottom.className = "card-bottom";
  const date = document.createElement("span");
  date.className = "memory-date";
  date.textContent = formatDate(memory.updated_at || memory.created_at);
  bottom.append(date);
  bottom.append(cardAction("History", () => openHistory(memory)));
  bottom.append(cardAction("Edit", () => openEditMemory(memory)));
  bottom.append(cardAction("Delete", () => confirmDeleteMemory(memory), "delete"));
  card.append(bottom);
  card.addEventListener("click", (event) => {
    if (!memory.id || event.target.closest("button, input, label, a, select, textarea")) return;
    setMemorySelected(memory.id, card, checkbox, !state.selectedIds.has(memory.id));
  });
  card.addEventListener("keydown", (event) => {
    if (!memory.id || event.target !== card || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    setMemorySelected(memory.id, card, checkbox, !state.selectedIds.has(memory.id));
  });
  return card;
}

function setMemorySelected(memoryId, card, checkbox, selected) {
  if (selected) state.selectedIds.add(memoryId);
  else state.selectedIds.delete(memoryId);
  checkbox.checked = selected;
  card.classList.toggle("selected", selected);
  card.setAttribute("aria-checked", String(selected));
  updateSelectionControls(visibleMemories());
}

function updateSelectionControls(records = visibleMemories()) {
  const loadedIds = new Set(state.memories.map((memory) => memory.id).filter(Boolean));
  for (const memoryId of state.selectedIds) {
    if (!loadedIds.has(memoryId)) state.selectedIds.delete(memoryId);
  }
  const visibleIds = records.map((memory) => memory.id).filter(Boolean);
  const selectedVisible = visibleIds.filter((memoryId) => state.selectedIds.has(memoryId)).length;
  ui.selectionBar.hidden = !state.activeScope || state.memories.length === 0;
  ui.selectedCount.textContent = `${state.selectedIds.size} selected`;
  ui.deleteSelected.disabled = state.selectedIds.size === 0;
  ui.removeDuplicates.disabled = state.memories.length < 2;
  ui.selectVisible.disabled = visibleIds.length === 0;
  ui.selectVisible.checked = visibleIds.length > 0 && selectedVisible === visibleIds.length;
  ui.selectVisible.indeterminate = selectedVisible > 0 && selectedVisible < visibleIds.length;
}

function toggleVisibleSelection() {
  const visibleIds = visibleMemories().map((memory) => memory.id).filter(Boolean);
  const allSelected = visibleIds.length > 0 && visibleIds.every((memoryId) => state.selectedIds.has(memoryId));
  for (const memoryId of visibleIds) {
    if (allSelected) state.selectedIds.delete(memoryId);
    else state.selectedIds.add(memoryId);
  }
  renderMemories();
}

function clearSelection() {
  state.selectedIds.clear();
  renderMemories();
}

function cardAction(label, handler, extra = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `card-button ${extra}`.trim();
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function formatDate(value) {
  if (!value) return "No timestamp";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function openNewMemory() {
  byId("memory-form").dataset.mode = "add";
  byId("memory-id-input").value = "";
  byId("memory-text-input").value = "";
  byId("infer-input").checked = false;
  byId("infer-row").hidden = false;
  byId("memory-modal-eyebrow").textContent = typeLabel(state.activeScope.type);
  byId("memory-modal-title").textContent = "Add memory";
  byId("memory-modal-copy").textContent = `Store a durable fact in ${state.activeScope.value}.`;
  byId("memory-submit").textContent = "Save memory";
  updateCharacterCount();
  byId("memory-dialog").showModal();
  byId("memory-text-input").focus();
}

function openEditMemory(memory) {
  byId("memory-form").dataset.mode = "edit";
  byId("memory-id-input").value = memory.id;
  byId("memory-text-input").value = memory.memory || memory.text || "";
  byId("infer-row").hidden = true;
  byId("memory-modal-eyebrow").textContent = "Exact update";
  byId("memory-modal-title").textContent = "Edit memory";
  byId("memory-modal-copy").textContent = "Updating the text also refreshes its vector embedding.";
  byId("memory-submit").textContent = "Update memory";
  updateCharacterCount();
  byId("memory-dialog").showModal();
  byId("memory-text-input").focus();
}

async function saveMemory() {
  const form = byId("memory-form");
  const text = byId("memory-text-input").value.trim();
  if (!text) return;
  const button = byId("memory-submit");
  button.disabled = true;
  try {
    if (form.dataset.mode === "edit") {
      const id = byId("memory-id-input").value;
      await api(`/memories/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ text }) });
      toast("Memory updated");
    } else {
      await api("/memories", {
        method: "POST",
        body: JSON.stringify({ text, infer: byId("infer-input").checked, ...scopePayload() }),
      });
      toast(byId("infer-input").checked ? "Mem0 extraction completed" : "Memory added");
    }
    byId("memory-dialog").close();
    await loadMemories();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function confirmDeleteMemory(memory) {
  state.pendingConfirm = async () => {
    await api(`/memories/${encodeURIComponent(memory.id)}`, { method: "DELETE" });
    toast("Memory deleted");
    await loadMemories();
  };
  byId("confirm-title").textContent = "Delete this memory?";
  byId("confirm-copy").textContent = "The vector and its stored text will be removed. Other memories are not affected.";
  byId("confirm-field").hidden = true;
  byId("confirm-submit").textContent = "Delete memory";
  byId("confirm-dialog").showModal();
}

function confirmDeleteSelected() {
  const memoryIds = [...state.selectedIds];
  if (!memoryIds.length || !state.activeScope) return;
  const count = memoryIds.length;
  const scope = state.activeScope.value;
  state.pendingConfirm = async () => {
    const payload = await api("/memories/delete_many", {
      method: "POST",
      body: JSON.stringify({ memory_ids: memoryIds, ...scopePayload() }),
    });
    state.selectedIds.clear();
    toast(`${payload.deleted_count || 0} selected memories deleted`);
    await loadMemories();
  };
  byId("confirm-title").textContent = `Delete ${count} selected ${count === 1 ? "memory" : "memories"}?`;
  byId("confirm-copy").textContent = `Only the selected memories in ${scope} will be removed. Unselected memories are not affected.`;
  byId("confirm-field").hidden = true;
  byId("confirm-submit").textContent = `Delete ${count}`;
  byId("confirm-dialog").showModal();
}

function confirmDeleteScope() {
  const expected = state.activeScope.value;
  state.pendingConfirm = async () => {
    if (byId("confirm-input").value !== expected) throw new Error("The scope value does not match.");
    const payload = await api("/memories/delete_all", { method: "POST", body: JSON.stringify(scopePayload()) });
    toast(`${payload.deleted_count || 0} memories deleted from ${expected}`);
    await loadMemories();
  };
  byId("confirm-title").textContent = "Delete all scope memories?";
  byId("confirm-copy").textContent = `This removes every memory in ${expected}. Other scopes remain intact.`;
  byId("confirm-field").hidden = false;
  byId("confirm-label").textContent = `Type ${expected} to confirm`;
  byId("confirm-input").value = "";
  byId("confirm-submit").textContent = "Delete all";
  byId("confirm-dialog").showModal();
}

function confirmRemoveDuplicates() {
  if (!state.activeScope || state.memories.length < 2) return;
  const scope = state.activeScope.value;
  state.pendingConfirm = async () => {
    const payload = await api("/memories/delete_duplicates", {
      method: "POST",
      body: JSON.stringify(scopePayload()),
    });
    const removed = payload.deleted_count || 0;
    toast(removed ? `Removed ${removed} exact duplicate ${removed === 1 ? "memory" : "memories"}` : "No exact duplicate memories found");
    state.selectedIds.clear();
    await loadMemories();
  };
  byId("confirm-title").textContent = "Remove exact duplicate memories?";
  byId("confirm-copy").textContent = `Only byte-for-byte-equivalent memory text (ignoring case and spacing) in ${scope} will be compared. The newest copy of each duplicate is kept; similar but distinct facts are not changed.`;
  byId("confirm-field").hidden = true;
  byId("confirm-submit").textContent = "Remove duplicates";
  byId("confirm-dialog").showModal();
}

async function openHistory(memory) {
  const list = byId("history-list");
  list.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "modal-copy";
  loading.textContent = "Loading history...";
  list.append(loading);
  byId("history-dialog").showModal();
  try {
    const payload = await api(`/memories/${encodeURIComponent(memory.id)}/history`);
    const entries = extractRecords(payload);
    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "modal-copy";
      empty.textContent = "No pod-local history is available for this memory.";
      list.append(empty);
      return;
    }
    for (const entry of entries) {
      const row = document.createElement("article");
      row.className = "history-entry";
      const event = document.createElement("strong");
      event.textContent = entry.event || "CHANGE";
      const time = document.createElement("time");
      time.textContent = formatDate(entry.updated_at || entry.created_at);
      const copy = document.createElement("p");
      copy.textContent = entry.new_memory || entry.old_memory || JSON.stringify(entry);
      row.append(event, time, copy);
      list.append(row);
    }
  } catch (error) {
    list.replaceChildren();
    const message = document.createElement("p");
    message.className = "notice";
    message.textContent = error.message;
    list.append(message);
  }
}

function exportMemories() {
  const data = {
    exported_at: new Date().toISOString(),
    scope: scopePayload(),
    view: state.view,
    memories: state.memories,
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `mem0-${state.activeScope.value.replace(/[^a-z0-9._-]+/gi, "-")}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function updateCharacterCount() {
  byId("character-count").textContent = String(byId("memory-text-input").value.length);
}

byId("connection-button").addEventListener("click", () => {
  byId("api-key-input").value = state.apiKey;
  byId("connection-dialog").showModal();
});
byId("add-scope-button").addEventListener("click", () => byId("scope-dialog").showModal());
byId("refresh-scopes-button").addEventListener("click", async () => {
  const button = byId("refresh-scopes-button");
  button.disabled = true;
  try { await discoverScopes({ notify: true }); }
  catch (error) { toast(error.message, "error"); }
  finally { updateControls(); }
});
byId("new-memory-button").addEventListener("click", openNewMemory);
byId("refresh-button").addEventListener("click", loadMemories);
byId("show-all-button").addEventListener("click", () => { ui.searchInput.value = ""; loadMemories(); });
byId("export-button").addEventListener("click", exportMemories);
byId("delete-scope-button").addEventListener("click", confirmDeleteScope);
ui.selectVisible.addEventListener("change", toggleVisibleSelection);
byId("clear-selection").addEventListener("click", clearSelection);
ui.deleteSelected.addEventListener("click", confirmDeleteSelected);
ui.removeDuplicates.addEventListener("click", confirmRemoveDuplicates);
byId("connection-close").addEventListener("click", () => byId("connection-dialog").close());
byId("connection-cancel").addEventListener("click", () => byId("connection-dialog").close());
byId("scope-close").addEventListener("click", () => byId("scope-dialog").close());
byId("scope-cancel").addEventListener("click", () => byId("scope-dialog").close());
byId("memory-close").addEventListener("click", () => byId("memory-dialog").close());
byId("memory-cancel").addEventListener("click", () => byId("memory-dialog").close());
byId("confirm-cancel").addEventListener("click", () => byId("confirm-dialog").close());
ui.filterInput.addEventListener("input", renderMemories);
ui.sortOrder.addEventListener("change", () => {
  state.sortOrder = ui.sortOrder.value === "oldest" ? "oldest" : "newest";
  localStorage.setItem("mem0-sort-order", state.sortOrder);
  renderMemories();
});
byId("memory-text-input").addEventListener("input", updateCharacterCount);
byId("history-close").addEventListener("click", () => byId("history-dialog").close());
byId("toggle-key-button").addEventListener("click", () => {
  const input = byId("api-key-input");
  input.type = input.type === "password" ? "text" : "password";
  byId("toggle-key-button").textContent = input.type === "password" ? "Show" : "Hide";
});

byId("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const query = ui.searchInput.value.trim();
  if (query) searchMemories(query);
});

byId("connection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const candidate = byId("api-key-input").value.trim();
  if (!candidate) return;
  const previous = state.apiKey;
  state.apiKey = candidate;
  byId("connect-submit").disabled = true;
  try {
    await api("/auth/check");
    sessionStorage.setItem("mem0-api-key", candidate);
    setConnected(true);
    try { await discoverScopes(); }
    catch (error) { toast(`Connected, but scope discovery failed: ${error.message}`, "error"); }
    byId("connection-dialog").close();
    toast("Connected to Mem0");
    updateControls();
    if (state.activeScope) await loadMemories();
  } catch (error) {
    state.apiKey = previous;
    setConnected(false);
    toast(error.message, "error");
  } finally {
    byId("connect-submit").disabled = false;
  }
});

byId("scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const scope = { type: byId("scope-type-input").value, value: byId("scope-value-input").value.trim(), saved: true };
  if (!scope.value) return;
  const existing = state.scopes.find((item) => item.type === scope.type && item.value === scope.value);
  if (!existing) state.scopes.push(scope);
  else existing.saved = true;
  saveScopes();
  renderScopes();
  byId("scope-dialog").close();
  byId("scope-value-input").value = "";
  selectScope(existing || scope);
});

byId("memory-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveMemory();
});

byId("confirm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.pendingConfirm) return;
  byId("confirm-submit").disabled = true;
  try {
    await state.pendingConfirm();
    state.pendingConfirm = null;
    byId("confirm-dialog").close();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    byId("confirm-submit").disabled = false;
  }
});

async function initialize() {
  ui.sortOrder.value = state.sortOrder;
  renderScopes();
  updateControls();
  if (state.apiKey) {
    try {
      await api("/auth/check");
      setConnected(true);
      try { await discoverScopes(); }
      catch (error) { toast(`Scope discovery failed: ${error.message}`, "error"); }
    }
    catch (_) { state.apiKey = ""; sessionStorage.removeItem("mem0-api-key"); setConnected(false); }
  }
  const last = localStorage.getItem("mem0-active-scope");
  const scope = state.scopes.find((item) => `${item.type}:${item.value}` === last);
  if (scope) await selectScope(scope);
  else if (!state.apiKey) byId("connection-dialog").showModal();
}

initialize();
