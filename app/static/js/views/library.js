// Library tab: stats, the list of saved models, and per-model actions.

import { $, $$, html, icon, render } from "../lib/dom.js";
import { api } from "../lib/api.js";
import * as format from "../lib/format.js";
import { isWeight } from "../lib/models.js";
import { state, emit } from "../state.js";
import { avatar, repoName, loading, errorBox, emptyState, goToButton } from "../ui/components.js";
import { toast, toastError, confirmDialog } from "../ui/overlay.js";

const list = () => $("#library-list");
const folderUrl = (name) => `/api/library/${encodeURIComponent(name)}`;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function loadLibrary() {
  if (!state.libraryLoaded) render(list(), loading("opening the hoard"));
  try {
    const data = await api.get("/api/library");
    Object.assign(state, {
      library: data.models, disk: data.disk, modelsDir: data.models_dir, problems: data.problems || [], libraryLoaded: true,
    });
    emit("status:changed");
    renderStats();
    renderFormatFilter();
    renderList();
  } catch (error) {
    render(list(), errorBox(error));
  }
}

// ---------- stats ----------

function renderStats() {
  const { library, disk } = state;
  const modelBytes = library.reduce((sum, m) => sum + m.size, 0);
  const otherBytes = Math.max(0, disk.used - modelBytes);
  const biggest = [...library].sort((a, b) => b.size - a.size)[0];

  const formatCounts = {};
  library.forEach((m) => m.formats.forEach((f) => { formatCounts[f] = (formatCounts[f] || 0) + 1; }));
  const formatSummary = Object.entries(formatCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => `${n} ${name}`)
    .join(" · ");

  $("#stat-count").textContent = library.length;
  $("#stat-count-detail").textContent = formatSummary || "nothing saved yet";
  $("#stat-size").textContent = format.bytes(modelBytes);
  $("#stat-size-detail").textContent = biggest
    ? `biggest: ${format.splitRepoId(biggest.repo_id)[1]} (${format.bytes(biggest.size)})`
    : " ";
  $("#stat-free").textContent = format.bytes(disk.free);
  $("#stat-free-detail").textContent = `${state.modelsDir} · ${format.bytes(disk.total)} total`;

  const meter = $("#disk-meter");
  meter.classList.toggle("low", disk.free / disk.total < 0.1);
  $(".mine", meter).style.width = `${(modelBytes / disk.total) * 100}%`;
  $(".other", meter).style.width = `${(otherBytes / disk.total) * 100}%`;
  meter.title = `Models: ${format.bytes(modelBytes)} · Other data: ${format.bytes(otherBytes)} · Free: ${format.bytes(disk.free)}`;
}

function renderFormatFilter() {
  const select = $("#library-format");
  const current = select.value;
  const formats = [...new Set(state.library.flatMap((m) => m.formats))].sort();
  render(select, html`
    <option value="">All formats</option>
    ${formats.map((f) => html`<option ${f === current && "selected"}>${f}</option>`)}`);
}

// ---------- list ----------

const SORTS = {
  recent: (a, b) => (b.downloaded_at || "").localeCompare(a.downloaded_at || ""),
  name: (a, b) => a.repo_id.localeCompare(b.repo_id),
  size: (a, b) => b.size - a.size,
};

function renderList() {
  if (!state.library.length) {
    render(list(), emptyState({
      doodle: "box",
      title: "The shelf is empty",
      text: "Find a model on Hugging Face and save it here.",
      action: goToButton("browse", "Browse models", { primary: true }),
    }));
    return;
  }

  const nameFilter = $("#library-filter").value.toLowerCase();
  const formatFilter = $("#library-format").value;
  const models = state.library
    .filter((m) => m.repo_id.toLowerCase().includes(nameFilter))
    .filter((m) => !formatFilter || m.formats.includes(formatFilter))
    .sort(SORTS[$("#library-sort").value]);

  // Keep expanded rows expanded across re-renders.
  const expanded = new Set($$(".item.open", list()).map((el) => el.dataset.name));

  render(list(), models.length
    ? html`<div class="list sk">${models.map(modelRow)}</div>`
    : emptyState({ doodle: "search", title: "Nothing matches", text: "No saved model matches this filter." }));

  for (const name of expanded) {
    const row = $(`.item[data-name="${CSS.escape(name)}"]`, list());
    if (row) toggleFiles(row, { open: true });
  }
}

function modelRow(model) {
  const update = state.updates[model.name];
  return html`
    <div class="item" data-name="${model.name}">
      <div class="row clickable" data-action="files">
        ${icon("chev", "chev")}
        ${avatar(model.repo_id)}
        <div class="row-main">
          <div class="name">${repoName(model.repo_id)}</div>
          <div class="sub">
            <span class="narrow-only"><b>${format.bytes(model.size)}</b></span>
            ${model.formats.map((f) => html`<span class="tag blue">${f}</span>`)}
            ${statusTags(model, update)}
            ${model.pipeline_tag && html`<span>${format.taskName(model.pipeline_tag)}</span>`}
            <span>${model.file_count} files</span>
            ${model.license && html`<span class="wide-only">${model.license}</span>`}
            ${model.downloaded_at && html`<span class="wide-only" title="${model.downloaded_at}">saved ${format.ago(model.downloaded_at)}</span>`}
          </div>
        </div>
        <div class="row-side">
          <span class="row-size">${format.bytes(model.size)}</span>
          ${rowActions(model, update)}
        </div>
      </div>
      <div class="files" hidden></div>
    </div>`;
}

function statusTags(model, update) {
  return [
    model.downloading && html`<span class="tag hl">downloading…</span>`,
    model.incomplete && !model.downloading && html`<span class="tag danger" title="The last download didn't finish. Resume it to complete the files.">unfinished</span>`,
    update?.checking && html`<span class="tag">checking…</span>`,
    update?.error && html`<span class="tag danger" title="${update.error}">check failed</span>`,
    update?.update_available && html`<span class="tag warn">update available!</span>`,
    update?.update_available === false && html`<span class="tag ok">${icon("check")}up to date</span>`,
    !model.managed && html`<span class="tag" title="This folder was added by hand, not downloaded through Hoard">added by hand</span>`,
    model.include && html`<span class="tag" title="Only some files of this repo were saved">partial</span>`,
    model.pickle && html`<span class="tag warn" title="${PICKLE_HINT}">pickle</span>`,
  ];
}

const PICKLE_HINT = "Has .bin/.pt/.pth files. These use Python pickle, which can run code when loaded with torch.load(). Prefer safetensors, or load with weights_only=True.";

function rowActions(model, update) {
  let main = null;
  if (model.incomplete && !model.downloading) {
    main = html`<button class="btn sm primary" data-action="update">${icon("retry")}Resume</button>`;
  } else if (update?.update_available) {
    main = html`<button class="btn sm primary" data-action="update">${icon("down")}Update</button>`;
  } else if (model.managed && !model.incomplete) {
    main = html`<button class="btn sm ghost icon wide-only" data-action="check" title="Check for update">${icon("refresh")}</button>`;
  }
  return html`
    ${main}
    <a class="btn sm ghost icon wide-only" href="https://huggingface.co/${model.repo_id}" target="_blank" rel="noopener" title="Open on Hugging Face">${icon("ext")}</a>
    <button class="btn sm ghost icon danger" data-action="delete" title="Delete" ${model.downloading && "disabled"}>${icon("trash")}</button>`;
}

// ---------- row actions ----------

async function toggleFiles(row, { open = !row.classList.contains("open") } = {}) {
  const panel = $(".files", row);
  row.classList.toggle("open", open);
  panel.hidden = !open;
  if (!open) return;

  if (!panel.dataset.loaded) render(panel, html`<div class="hint">loading…</div>`);
  try {
    const files = await api.get(`${folderUrl(row.dataset.name)}/files`);
    const model = state.library.find((m) => m.name === row.dataset.name);
    panel.dataset.loaded = "1";
    render(panel, html`
      <div class="hint mono">${state.modelsDir}/${row.dataset.name}/</div>
      ${files.map((f) => html`
        <div class="file-row ${isWeight(f.path) && "weight"}">
          ${icon(isWeight(f.path) ? "cube" : "file")}
          <span class="path mono">${f.path}</span>
          ${f.pickle && html`<span class="tag warn" title="${PICKLE_HINT}">pickle</span>`}
          <span class="size">${format.bytes(f.size)}</span>
        </div>`)}
      ${!files.length && html`<div class="hint">empty folder</div>`}
      <div class="verify" data-verify>${model && verifyBox(model)}</div>`);
  } catch (error) {
    render(panel, errorBox(error));
  }
}

// ---------- checking files against Hugging Face ----------

const VERIFY_RUNNING = ["waiting", "running"];

function verifyBox(model) {
  if (!model.managed || model.incomplete || model.downloading) return null;
  const run = state.verifies[model.name];
  const checkButton = (label) => html`<button class="btn sm" data-action="verify">${icon("check")}${label}</button>`;

  if (!run) {
    return html`${checkButton("Check files")}<span class="hint">re-reads every file and compares it with Hugging Face's hashes</span>`;
  }
  if (VERIFY_RUNNING.includes(run.status)) {
    const percent = run.total_bytes ? (run.checked_bytes / run.total_bytes) * 100 : 0;
    return html`
      <span class="hint">${run.status === "waiting" ? "waiting for another check to finish…" : `checking… ${percent.toFixed(0)}%`}</span>
      <div class="progress sk ${percent < 0.5 && "waiting"}"><div style="width:${percent}%"></div></div>`;
  }
  if (run.status === "error") {
    return html`<div class="notice danger sk thin">${icon("alert")}<div>Couldn't check: ${run.error}</div></div>${checkButton("Try again")}`;
  }
  if (!run.problems.length) {
    return html`<div class="notice ok sk thin">${icon("check")}<div>All ${run.file_count} files match Hugging Face.</div></div>${checkButton("Check again")}`;
  }
  return html`
    <div class="notice danger sk thin">${icon("alert")}<div>
      <b>${run.problems.length} of ${run.file_count} files have a problem:</b>
      <ul class="problems">${run.problems.map((p) => html`<li><span class="mono">${p.path}</span>: ${p.issue}</li>`)}</ul>
      <button class="btn sm primary" data-action="repair">${icon("down")}Download them again</button>
    </div></div>`;
}

function renderVerify(name) {
  const box = $(`.item[data-name="${CSS.escape(name)}"] [data-verify]`, list());
  const model = state.library.find((m) => m.name === name);
  if (box && model) render(box, verifyBox(model));
}

async function verifyModel(name) {
  state.verifies[name] = await api.post(`${folderUrl(name)}/verify`);
  renderVerify(name);
  while (VERIFY_RUNNING.includes(state.verifies[name]?.status)) {
    await sleep(1000);
    try {
      state.verifies[name] = await api.get(`${folderUrl(name)}/verify`);
    } catch (error) {
      state.verifies[name] = { status: "error", error: error.message };
    }
    renderVerify(name);
  }
}

async function repairModel(model, button) {
  button.disabled = true;
  await api.post(`${folderUrl(model.name)}/repair`);
  delete state.verifies[model.name];
  toast(`Downloading the damaged files of ${model.repo_id} again`);
  emit("downloads:changed");
  loadLibrary();
}

// ---------- updates ----------

async function checkForUpdate(name) {
  state.updates[name] = { checking: true };
  renderList();
  try {
    state.updates[name] = await api.get(`${folderUrl(name)}/check`);
  } catch (error) {
    state.updates[name] = { error: error.message };
  }
  renderList();
}

async function checkAllForUpdates(button) {
  button.disabled = true;
  const managed = state.library.filter((m) => m.managed && !m.incomplete);
  for (const model of managed) await checkForUpdate(model.name);
  button.disabled = false;
  const available = managed.filter((m) => state.updates[m.name]?.update_available).length;
  toast(available ? `${available} update${available > 1 ? "s" : ""} available` : "Everything is up to date");
}

async function updateModel(model, button) {
  button.disabled = true;
  await api.post(`${folderUrl(model.name)}/update`);
  delete state.updates[model.name];
  toast(model.incomplete ? `Resuming ${model.repo_id}` : `Updating ${model.repo_id}`);
  emit("downloads:changed");
  loadLibrary();
}

async function deleteModel(model) {
  const confirmed = await confirmDialog({
    title: "Throw this one out?",
    message: html`<b>${model.repo_id}</b> (${format.bytes(model.size)}) will be deleted from the disk for good.`,
    confirmLabel: "Delete it",
    danger: true,
  });
  if (!confirmed) return;
  await api.delete(folderUrl(model.name));
  toast(`Deleted ${model.repo_id}`);
  loadLibrary();
}

async function onListClick(event) {
  const target = event.target.closest("[data-action]");
  const row = target?.closest(".item");
  if (!target || !row || target.tagName === "A") return;

  const action = target.dataset.action;
  const model = state.library.find((m) => m.name === row.dataset.name);
  if (action !== "files") event.stopPropagation();

  try {
    if (action === "files") await toggleFiles(row);
    if (action === "check") await checkForUpdate(model.name);
    if (action === "update") await updateModel(model, target);
    if (action === "delete") await deleteModel(model);
    if (action === "verify") await verifyModel(model.name);
    if (action === "repair") await repairModel(model, target);
  } catch (error) {
    toastError(error);
    target.disabled = false;
  }
}

export function initLibrary() {
  ["#library-filter", "#library-format", "#library-sort"].forEach((sel) => $(sel).addEventListener("input", renderList));
  $("#check-all").addEventListener("click", (event) => checkAllForUpdates(event.currentTarget));
  list().addEventListener("click", onListClick);
}
