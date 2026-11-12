// Settings dialog: the Hugging Face token.
// The server never sends the token back; we only see who it belongs to.

import { $, html, icon } from "../lib/dom.js";
import { api } from "../lib/api.js";
import { state } from "../state.js";
import { avatar } from "../ui/components.js";
import { openModal, updateModal, isModalOpen, toast, toastError } from "../ui/overlay.js";

const TOKEN_URL = "/api/settings/token";

export async function loadToken() {
  try {
    state.token = await api.get(TOKEN_URL);
  } catch {
    state.token = null;
  }
  return state.token;
}

export async function openSettings() {
  openModal(html`
    ${closeButton()}
    <h3>Hugging Face token</h3>
    <div class="loading">checking<span>.</span><span>.</span><span>.</span></div>`, { wide: true });
  await loadToken();
  if (isModalOpen()) renderSettings();
}

const closeButton = () => html`<button class="btn ghost icon close" data-close title="Close">${icon("x")}</button>`;
const roleName = (role) => (role === "fineGrained" ? "fine-grained" : role || "unknown");

function renderSettings() {
  const token = state.token || {};
  updateModal(html`
    ${closeButton()}
    <h3>Hugging Face token</h3>
    ${tokenStatus(token)}
    ${token.valid && token.role && token.role !== "read" && html`
      <div class="notice info sk thin">${icon("alert")}<div>
        This token has <b>${roleName(token.role)}</b> access. Hoard only reads, so a <b>Read</b> token is the safer choice.
      </div></div>`}
    ${token.ui_writable === false ? envTokenNotice() : tokenForm(token)}
    <ul class="fine-print">
      <li>Make one at <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener">huggingface.co/settings/tokens</a>. The <b>Read</b> type is enough.</li>
      <li>It's checked with Hugging Face, then kept only on the server (an owner-only file in <span class="mono">/config</span>). It's never sent back to the browser.</li>
      ${!token.protected && html`
        <li class="warn">This page has no password, so anyone on your network could download using the saved token.
          Set <span class="mono">APP_PASSWORD</span> on the server to lock it.</li>`}
    </ul>`);
  bindForm();
}

function tokenStatus(token) {
  if (!token.source) {
    return html`
      <div class="token-status sk thin">${icon("key")}<div>
        <div class="who">No token yet</div>
        <div class="meta">Public models download fine. Gated ones (Llama, Gemma…) need a token.</div>
      </div></div>`;
  }
  if (token.valid === false) {
    return html`
      <div class="notice note">${icon("alert")}<div>
        <b>Hugging Face rejected the saved token</b> (${token.hint}). It may have been revoked; replace it below.
      </div></div>`;
  }
  return html`
    <div class="token-status sk thin">
      ${avatar(token.user || "?")}
      <div>
        <div class="who">Signed in as ${token.user}</div>
        <div class="meta">
          <span class="mono">${token.hint}</span> ·
          ${token.token_name && html`${token.token_name} · `}${roleName(token.role)} access ·
          ${token.source === "env" ? "from HF_TOKEN on the server" : "saved on the server"}
        </div>
      </div>
    </div>`;
}

const envTokenNotice = () => html`
  <div class="notice info sk thin">${icon("lock")}<div>
    This token comes from the <span class="mono">HF_TOKEN</span> setting on the server, so it can't be changed here.
    Remove it from the server config to manage it from this page.
  </div></div>`;

const tokenForm = (token) => html`
  <label class="field sk thin token-field">
    ${icon("key")}
    <input type="password" id="token-input" placeholder="${token.source ? "paste a new token to replace it" : "hf_…"}"
           autocomplete="off" autocapitalize="off" spellcheck="false">
    <button type="button" class="btn ghost sm icon" id="token-peek" title="Show / hide">${icon("eye")}</button>
  </label>
  <div class="actions">
    ${token.source === "ui" && html`<button class="btn danger" id="token-remove">${icon("trash")}Remove</button>`}
    <button class="btn primary" id="token-save">${icon("check")}<span>Check &amp; save</span></button>
  </div>`;

function bindForm() {
  const input = $("#token-input");
  if (!input) return;   // token comes from HF_TOKEN; nothing to edit
  input.focus();

  $("#token-peek").onclick = () => {
    input.type = input.type === "password" ? "text" : "password";
    input.focus();
  };
  input.onkeydown = (event) => { if (event.key === "Enter") $("#token-save").click(); };
  $("#token-save").onclick = (event) => saveToken(input, event.currentTarget);
  $("#token-remove")?.addEventListener("click", removeToken);
}

async function saveToken(input, button) {
  const token = input.value.trim();
  if (!token) {
    input.focus();
    toast("Paste a token first", { error: true });
    return;
  }
  const label = $("span", button);
  button.disabled = true;
  label.textContent = "Checking…";
  try {
    const result = await api.put(TOKEN_URL, { token });
    input.value = "";
    toast(`Token saved, signed in as ${result.user}`);
    await loadToken();
    renderSettings();
  } catch (error) {
    toastError(error);
    button.disabled = false;
    label.textContent = "Check & save";
  }
}

async function removeToken() {
  try {
    await api.delete(TOKEN_URL);
    toast("Token removed");
    await loadToken();
    renderSettings();
  } catch (error) {
    toastError(error);
  }
}
