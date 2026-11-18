// Warnings shown under the top bar: server setup problems, and a missing password.

import { $, html, icon, render } from "../lib/dom.js";
import { storage } from "../lib/api.js";
import { state } from "../state.js";

const HIDE_KEY = "hide-no-password-warning";

export function renderBanner() {
  const banner = $("#banner");
  const problems = state.problems || [];
  const noPassword = state.token && state.token.protected === false && !storage.get(HIDE_KEY);

  render(banner, html`
    ${problems.map((problem) => html`
      <div class="notice danger sk thin">${icon("alert")}<div><b>Setup problem.</b> ${problem}</div></div>`)}
    ${noPassword && html`
      <div class="notice note">${icon("lock")}<div>
        <b>No password set.</b> Anyone on your network can use Hoard${state.token.source ? ", including your Hugging Face token" : ""}.
        Set <span class="mono">APP_PASSWORD</span> on the server to lock it.
        <button class="btn sm ghost" data-hide-banner>Hide</button>
      </div></div>`}`);
  banner.hidden = !problems.length && !noPassword;
}

export function initBanner() {
  $("#banner").addEventListener("click", (event) => {
    if (!event.target.closest("[data-hide-banner]")) return;
    storage.set(HIDE_KEY, "1");
    renderBanner();
  });
}
