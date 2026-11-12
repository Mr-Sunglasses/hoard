// Model panel (drawer): repo facts, helpful notices, and the file picker.

import { $, $$, html, icon, render } from "../lib/dom.js";
import { api, withQuery } from "../lib/api.js";
import * as format from "../lib/format.js";
import { isWeight, isGguf, isSafetensors, quantizations, presets, defaultSelection, DEFAULT_QUANT } from "../lib/models.js";
import { state, emit } from "../state.js";
import { avatar, loading, errorBox } from "../ui/components.js";
import { openDrawer, closeDrawer, isDrawerOpen, toast, toastError } from "../ui/overlay.js";
import { loadToken, openSettings } from "./settings.js";

const MAX_QUANT_CHIPS = 8;

export async function openModelPanel(repoId) {
  const drawer = openDrawer(html`${header(repoId)}<div class="panel-body">${loading("reading the file list")}</div>`);
  try {
    const [repo] = await Promise.all([
      api.get(withQuery("/api/repo", { repo_id: repoId })),
      state.token ? null : loadToken(),
    ]);
    if (isDrawerOpen()) new FilePicker(drawer, repo).render();
  } catch (error) {
    render($(".panel-body", drawer), errorBox(error));
  }
}

function header(repoId, facts = null) {
  const [org, name] = format.splitRepoId(repoId);
  return html`
    <div class="panel-head">
      <div class="panel-title">
        ${avatar(repoId)}
        <h3>${org && html`<span class="org">${org} /</span><br>`}${name}</h3>
        <a class="btn sm ghost icon" href="https://huggingface.co/${repoId}" target="_blank" rel="noopener" title="Open on Hugging Face">${icon("ext")}</a>
        <button class="btn sm ghost icon" data-close title="Close">${icon("x")}</button>
      </div>
      ${facts}
    </div>`;
}

const sumSizes = (files) => files.reduce((sum, f) => sum + f.size, 0);

/** The file picker for one repo. Keeps which files are ticked and the active preset chip. */
class FilePicker {
  constructor(drawer, repo) {
    this.drawer = drawer;
    this.repo = repo;
    this.paths = repo.files.map((f) => f.path);
    this.quants = quantizations(this.paths);

    const { preset, keep } = defaultSelection(this.paths);
    this.preset = preset;
    this.selected = new Set(this.paths.filter(keep));
  }

  render() {
    const { repo } = this;
    const weights = repo.files.filter((f) => isWeight(f.path));
    const others = repo.files.filter((f) => !isWeight(f.path));

    render(this.drawer, html`
      ${header(repo.id, this.facts())}
      <div class="panel-body">
        ${this.notices()}
        ${this.presetChips()}
        <label class="field sk thin pattern">${icon("search")}<input type="text" data-pattern placeholder="tick weights matching a pattern, e.g. *Q5_K_M*"></label>
        ${this.fileGroup("Weights", weights)}
        ${this.fileGroup("Configs & the rest", others)}
      </div>
      <div class="panel-foot">
        <div class="selection"><div class="total" data-total></div><div class="detail" data-detail></div></div>
        <button class="btn primary" data-download>${icon("down")}Add to hoard</button>
      </div>`);

    this.bindEvents();
    this.refresh();
  }

  // ---------- sections ----------

  facts() {
    const { repo } = this;
    const fact = (label, value) => html`
      <div class="fact sk"><div class="label">${label}</div><div class="value" title="${value}">${value}</div></div>`;
    return html`
      <div class="facts">
        ${fact("task", format.taskName(repo.pipeline_tag) || "–")}
        ${fact("license", repo.license || "–")}
        ${fact("downloads", format.count(repo.downloads))}
        ${fact("whole repo", format.bytes(sumSizes(repo.files)))}
      </div>`;
  }

  notices() {
    const { repo, paths, quants } = this;
    const ggufCount = paths.filter(isGguf).length;
    const mixedFormats = paths.some(isSafetensors) && paths.some((p) => isWeight(p) && !isSafetensors(p));
    const token = state.token;
    const hasToken = token?.source && token.valid !== false;

    return html`
      ${repo.gated && html`
        <div class="notice note">${icon("lock")}<div>
          <b>Gated model.</b> Accept its terms on
          <a href="https://huggingface.co/${repo.id}" target="_blank" rel="noopener">its Hugging Face page</a> first.
          ${hasToken
            ? html`Your token (${token.user}) will be used.`
            : html`You also need a Hugging Face token.<br><button class="btn sm" data-open-settings>${icon("key")}Add token</button>`}
        </div></div>`}

      ${repo.in_library && html`
        <div class="notice ok sk thin">${icon("check")}<div>Already on your shelf. Ticked files that are missing will be added.</div></div>`}

      ${ggufCount > 1 && html`
        <div class="notice info sk thin">${icon("cube")}<div>
          ${ggufCount} GGUF files here, one per quantization.
          ${quants.includes(DEFAULT_QUANT)
            ? html`<b>${DEFAULT_QUANT}</b> is ticked: a good balance of size and quality. Bigger (Q5, Q6, Q8) stays closer to the original; smaller (Q3, Q2) loses more.`
            : "Pick the quantization you want."}
        </div></div>`}

      ${ggufCount <= 1 && mixedFormats && html`
        <div class="notice info sk thin">${icon("cube")}<div>Same weights in several formats here. Only <b>safetensors</b> is ticked to save space.</div></div>`}`;
  }

  presetChips() {
    const chip = (preset, label) => html`<button class="chip" data-preset="${preset}">${label}</button>`;
    const quantChips = this.quants.length > 1 ? this.quants.slice(0, MAX_QUANT_CHIPS).map((q) => chip(`quant:${q}`, q)) : [];
    return html`
      <div class="presets">
        ${chip("all", "everything")}
        ${this.paths.some(isSafetensors) && chip("safetensors", "safetensors + configs")}
        ${quantChips}
        ${chip("configsOnly", "configs only")}
      </div>`;
  }

  fileGroup(title, files) {
    if (!files.length) return null;
    return html`
      <div class="group-head">
        <span class="title">${title}</span>
        <span class="total">${files.length} files · ${format.bytes(sumSizes(files))}</span>
      </div>
      ${files.map((f) => html`
        <label class="file-row ${isWeight(f.path) && "weight"}">
          <input type="checkbox" data-path="${f.path}" ${this.selected.has(f.path) && "checked"}>
          ${icon(isWeight(f.path) ? "cube" : "file")}
          <span class="path mono">${f.path}</span>
          <span class="size">${format.bytes(f.size)}</span>
        </label>`)}`;
  }

  // ---------- behaviour ----------

  /** Tick exactly the files the predicate keeps, and highlight a chip. */
  select(preset, keep) {
    this.preset = preset;
    this.selected = new Set(this.paths.filter(keep));
    $$("input[data-path]", this.drawer).forEach((box) => { box.checked = this.selected.has(box.dataset.path); });
    this.refresh();
  }

  applyPreset(preset) {
    $("[data-pattern]", this.drawer).value = "";
    const [kind, arg] = preset.split(":");
    this.select(preset, presets[kind](arg));
  }

  /** Update the footer, the download button and the active chip. */
  refresh() {
    const chosen = this.repo.files.filter((f) => this.selected.has(f.path));
    const size = sumSizes(chosen);
    const free = state.disk?.free;
    const tooBig = free != null && size > free;

    $("[data-total]", this.drawer).textContent = format.bytes(size);
    const detail = $("[data-detail]", this.drawer);
    detail.classList.toggle("too-big", tooBig);
    detail.textContent = tooBig
      ? `won't fit: only ${format.bytes(free)} free`
      : `${chosen.length} of ${this.paths.length} files${free != null ? ` · ${format.bytes(free)} free` : ""}`;

    $("[data-download]", this.drawer).disabled = !chosen.length || tooBig;
    $$(".chip", this.drawer).forEach((chip) => chip.classList.toggle("on", chip.dataset.preset === this.preset));
  }

  async download(button) {
    button.disabled = true;
    const include = this.selected.size === this.paths.length ? null : [...this.selected];
    try {
      await api.post("/api/downloads", { repo_id: this.repo.id, include });
      toast(`Downloading ${this.repo.id}`);
      closeDrawer();
      emit("downloads:changed");
    } catch (error) {
      toastError(error);
      button.disabled = false;
    }
  }

  bindEvents() {
    const drawer = this.drawer;
    $$("[data-preset]", drawer).forEach((chip) => { chip.onclick = () => this.applyPreset(chip.dataset.preset); });
    $("[data-open-settings]", drawer)?.addEventListener("click", openSettings);
    $("[data-download]", drawer).onclick = (event) => this.download(event.currentTarget);

    $("[data-pattern]", drawer).oninput = (event) => {
      const glob = event.target.value.trim();
      if (glob) this.select("", presets.pattern(glob));
    };

    $$("input[data-path]", drawer).forEach((box) => {
      box.onchange = () => {
        box.checked ? this.selected.add(box.dataset.path) : this.selected.delete(box.dataset.path);
        this.preset = "";
        this.refresh();
      };
    });
  }
}
