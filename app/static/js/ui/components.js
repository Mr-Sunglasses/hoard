// Small reusable templates: avatars, repo names, loading and empty states.

import { html, icon, raw } from "../lib/dom.js";
import { splitRepoId } from "../lib/format.js";

/** Round hand-drawn avatar with the org's first letter; color derived from the name. */
export function avatar(repoId) {
  const org = splitRepoId(repoId)[0] || repoId;
  let hue = 0;
  for (const char of org) hue = (hue * 31 + char.charCodeAt(0)) % 360;
  return html`<div class="avatar" style="--h:${hue}">${org[0] || "?"}</div>`;
}

/** "org / name" with the org dimmed. */
export function repoName(repoId) {
  const [org, name] = splitRepoId(repoId);
  return org ? html`<span class="org">${org} / </span>${name}` : html`${name}`;
}

/** Handwritten "loading..." with wiggling dots. */
export const loading = (text) =>
  html`<div class="list sk"><div class="loading">${text}<span>.</span><span>.</span><span>.</span></div></div>`;

export const errorBox = (error) => html`<div class="error-box sk thin">${error.message || error}</div>`;

/** A button that switches tab (handled in main.js). */
export const goToButton = (tab, label, { primary = false } = {}) =>
  html`<button class="btn ${primary ? "primary" : ""}" data-goto="${tab}">${icon("search")}${label}</button>`;

/** Empty state with a doodle, a title, a line of text and an optional action. */
export const emptyState = ({ doodle, title, text, action = null }) => html`
  <div class="list sk">
    <div class="empty">${DOODLES[doodle]}<h3>${title}</h3><p>${text}</p>${action}</div>
  </div>`;

// Hand-drawn illustrations for empty states (stroke follows the text color).
const doodle = (paths) => raw(`<svg class="doodle" viewBox="0 0 170 120" fill="none" stroke="currentColor"
  stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`);

const DOODLES = {
  // an open, empty box
  box: doodle(`
    <path d="M45 52c14-1 29-1 43 0l38-1c1 17 0 34 1 51-27 2-54 2-81 0-1-17-1-34-1-50z"/>
    <path d="M46 52c-6-7-11-14-17-21 15-1 30-1 45 0 5 7 10 14 14 21"/>
    <path d="M126 51c5-7 11-14 16-21-15-1-31-1-46 0-3 7-6 14-8 21"/>
    <path d="M88 52c0 16 0 33 1 50" opacity=".5"/>
    <path d="M60 70c8 0 15 0 22 .5M60 80c5 0 10 0 14 .3" opacity=".5"/>
    <path d="M85 8c-1 5 0 10 0 15M78 16c2 2 5 5 7 7 2-2 5-5 7-7" stroke-width="2"/>
    <path d="M118 14c2 1 3 2 5 3M126 24c2 0 4 1 6 1M50 16c-2 1-3 2-5 3" opacity=".6"/>`),
  // a magnifying glass with a question mark
  search: doodle(`
    <path d="M72 22c17-1 31 12 31 29 0 16-13 30-30 30-17 1-31-12-31-29-1-16 12-29 28-30 2 0 5 0 7 1"/>
    <path d="M97 74c8 8 17 16 25 25M101 71c7 9 15 17 23 26"/>
    <path d="M62 42c3-4 8-6 13-6" opacity=".5"/>
    <path d="M128 30c3-2 5-3 8-4M131 42c3 0 6 0 9 1M30 82c-3 1-5 3-8 4" opacity=".6"/>
    <path d="M67 47c0-4 3-7 7-7 3 0 6 3 6 6 0 4-5 4-6 8M74 60v1"/>`),
  // an arrow into a tray
  download: doodle(`
    <path d="M85 14c1 18 0 37 1 55M69 54c6 6 11 12 17 17 6-5 12-11 18-17"/>
    <path d="M48 92c25-1 50-1 74 0"/>
    <path d="M40 92c0 4 0 8 1 12 29 1 59 1 88 0 1-4 1-8 0-12" opacity=".55"/>
    <path d="M128 28c3-2 5-3 8-4M131 40c3 0 6 0 9 1M38 30c-3-1-5-3-8-4" opacity=".6"/>`),
};
