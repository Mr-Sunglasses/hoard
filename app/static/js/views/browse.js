// Browse tab: search the Hugging Face Hub and open a model's panel.

import { $, html, icon, render } from "../lib/dom.js";
import { api, withQuery } from "../lib/api.js";
import * as format from "../lib/format.js";
import { state } from "../state.js";
import { avatar, repoName, loading, errorBox, emptyState } from "../ui/components.js";
import { openModelPanel } from "./model-panel.js";

const SEARCH_DELAY_MS = 400;
const REPO_ID = /^[\w.-]+\/[\w.-]+$/;

let latestSearch = 0;   // ignore responses that arrive after a newer search started
let typingTimer;

export async function runSearch() {
  state.searched = true;
  const query = $("#search-query").value.trim();
  const task = $("#search-task").value;
  const sort = $("#search-sort").value;
  const searchId = ++latestSearch;

  $("#search-caption").textContent = "";
  render($("#search-results"), loading("asking Hugging Face"));

  try {
    const results = await api.get(withQuery("/api/search", { q: query, task, sort, limit: 40 }));
    if (searchId !== latestSearch) return;

    // A pasted repo id that search didn't surface can still be opened directly.
    const exactIdMissing = REPO_ID.test(query) && !results.some((r) => r.id.toLowerCase() === query.toLowerCase());
    if (exactIdMissing) results.unshift({ id: query, exact: true });

    $("#search-caption").textContent = caption(query, task, results.length);
    render($("#search-results"), results.length
      ? html`<div class="list sk">${results.map(resultRow)}</div>`
      : emptyState({ doodle: "search", title: "No models found", text: "Try different words or another task." }));
  } catch (error) {
    if (searchId === latestSearch) render($("#search-results"), errorBox(error));
  }
}

function caption(query, task, count) {
  if (query) return `${count} results for “${query}”`;
  const sortLabel = $("#search-sort").selectedOptions[0].text.toLowerCase();
  return `${sortLabel}${task ? " · " + format.taskName(task).toLowerCase() : ""} on Hugging Face →`;
}

function resultRow(model) {
  const saved = state.library.some((m) => m.repo_id === model.id);
  return html`
    <div class="item">
      <div class="row clickable" data-repo="${model.id}">
        ${avatar(model.id)}
        <div class="row-main">
          <div class="name">${repoName(model.id)}</div>
          <div class="sub">
            ${saved && html`<span class="tag ok">${icon("check")}on your shelf</span>`}
            ${model.exact && html`<span class="tag">open by repo id</span>`}
            ${model.pipeline_tag && html`<span class="tag">${format.taskName(model.pipeline_tag)}</span>`}
            ${model.library_name && html`<span class="wide-only">${model.library_name}</span>`}
            ${model.last_modified && html`<span class="wide-only">updated ${format.ago(model.last_modified)}</span>`}
          </div>
        </div>
        <div class="row-side sub result-stats">
          ${model.downloads != null && html`<span class="counter" title="Downloads (last 30 days)">${icon("down")}${format.count(model.downloads)}</span>`}
          ${model.likes != null && html`<span class="counter wide-only" title="Likes">${icon("heart")}${format.count(model.likes)}</span>`}
          ${icon("chev", "chev")}
        </div>
      </div>
    </div>`;
}

export function initBrowse() {
  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    clearTimeout(typingTimer);
    runSearch();
  });
  $("#search-query").addEventListener("input", () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(runSearch, SEARCH_DELAY_MS);
  });
  $("#search-task").addEventListener("change", runSearch);
  $("#search-sort").addEventListener("change", runSearch);
  $("#search-results").addEventListener("click", (event) => {
    const row = event.target.closest("[data-repo]");
    if (row) openModelPanel(row.dataset.repo);
  });
}
