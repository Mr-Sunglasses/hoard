// Overlays: sticky-note toasts, the taped modal, and the side drawer.
// They share one backdrop; the modal can open on top of the drawer.

import { $, html, icon, render } from "../lib/dom.js";

const backdrop = () => $("#backdrop");
const modal = () => $("#modal");
const drawer = () => $("#drawer");

const MODAL_ANIMATION_MS = 180;
const DRAWER_ANIMATION_MS = 250;

// ---------- toasts ----------

export function toast(message, { error = false } = {}) {
  const note = document.createElement("div");
  note.className = error ? "toast error" : "toast";
  render(note, html`${icon(error ? "alert" : "check")}<span>${message}</span>`);
  $("#toasts").append(note);
  setTimeout(() => note.remove(), 4000);
}

export const toastError = (error) => toast(error.message || String(error), { error: true });

// ---------- shared show/hide ----------

function show(element) {
  element.hidden = false;
  backdrop().hidden = false;
  requestAnimationFrame(() => {
    element.classList.add("show");
    backdrop().classList.add("show");
  });
}

function hide(element, delay, keepBackdrop) {
  element.classList.remove("show");
  if (!keepBackdrop) backdrop().classList.remove("show");
  setTimeout(() => {
    element.hidden = true;
    if (!keepBackdrop) backdrop().hidden = true;
  }, delay);
}

// ---------- modal ----------

let onModalDismiss = null;

/** Open the modal with the given content. Returns the modal element. */
export function openModal(content, { wide = false } = {}) {
  const element = modal();
  element.classList.toggle("wide", wide);
  render(element, html`<span class="tape"></span>${content}`);
  show(element);
  return element;
}

/** Replace the content of an open modal (keeps it open). */
export function updateModal(content) {
  render(modal(), html`<span class="tape"></span>${content}`);
}

export function closeModal() {
  if (modal().hidden) return;
  const dismiss = onModalDismiss;
  onModalDismiss = null;
  dismiss?.();
  hide(modal(), MODAL_ANIMATION_MS, !drawer().hidden);
}

export const isModalOpen = () => !modal().hidden;

/** Ask a yes/no question. Resolves true only when the confirm button is clicked. */
export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const element = openModal(html`
      <h3>${title}</h3>
      <p>${message}</p>
      <div class="actions">
        <button class="btn" data-answer="no">Never mind</button>
        <button class="btn ${danger ? "danger-solid" : "primary"}" data-answer="yes">${confirmLabel}</button>
      </div>`);
    onModalDismiss = () => resolve(false);
    element.querySelectorAll("[data-answer]").forEach((button) => {
      button.onclick = () => {
        onModalDismiss = null;
        resolve(button.dataset.answer === "yes");
        closeModal();
      };
    });
    $('[data-answer="no"]', element).focus();
  });
}

// ---------- drawer ----------

export function openDrawer(content) {
  render(drawer(), content);
  show(drawer());
  return drawer();
}

export function closeDrawer() {
  if (drawer().hidden || isModalOpen()) return;
  hide(drawer(), DRAWER_ANIMATION_MS, false);
}

export const isDrawerOpen = () => !drawer().hidden;

// ---------- wiring ----------

/** Close the topmost overlay with Escape or a click on the backdrop. */
export function initOverlays() {
  const closeTopmost = () => (isModalOpen() ? closeModal() : closeDrawer());
  backdrop().addEventListener("click", closeTopmost);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeTopmost();
  });
  // Any [data-close] button inside an overlay closes it.
  modal().addEventListener("click", (event) => { if (event.target.closest("[data-close]")) closeModal(); });
  drawer().addEventListener("click", (event) => { if (event.target.closest("[data-close]")) closeDrawer(); });
}
