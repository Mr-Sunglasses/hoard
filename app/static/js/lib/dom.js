// DOM helpers and a tiny, safe HTML templating tag.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** Markup that has already been escaped (the result of html`` or raw()). */
class SafeHtml {
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeText = (text) => String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);

function renderValue(value) {
  if (value == null || value === false || value === true) return "";
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(renderValue).join("");
  return escapeText(value);
}

/**
 * Template tag that escapes every interpolated value, so data from the API
 * can't inject markup. Nested html`` results, arrays of them, and raw()
 * strings are inserted as-is. null / undefined / booleans render nothing,
 * which makes `${cond && html`...`}` work for optional bits.
 *
 *   html`<span title="${name}">${name}</span>`
 */
export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, i) => { out += renderValue(value) + strings[i + 1]; });
  return new SafeHtml(out);
}

/** Mark a trusted, hand-written string as markup. Never use it with API data. */
export const raw = (markup) => new SafeHtml(markup);

/** Replace an element's content with a template. */
export function render(element, template) {
  element.innerHTML = renderValue(template);
}

/** An icon from icons.svg, e.g. icon("trash"). */
export const icon = (name, extraClass = "") =>
  raw(`<svg class="i ${extraClass}"><use href="/static/icons.svg#i-${name}"/></svg>`);
