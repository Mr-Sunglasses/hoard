// Entry point: theme toggle, tabs, keyboard shortcuts, and wiring the views together.

import { $, $$, icon, render } from "./lib/dom.js";
import { storage } from "./lib/api.js";
import { state, on } from "./state.js";
import { initOverlays } from "./ui/overlay.js";
import { initBanner, renderBanner } from "./ui/banner.js";
import { initLibrary, loadLibrary } from "./views/library.js";
import { initBrowse, runSearch } from "./views/browse.js";
import { initDownloads, pollDownloads, renderDownloads, POLL_INTERVAL_MS } from "./views/downloads.js";
import { loadToken, openSettings } from "./views/settings.js";

const TABS = ["library", "browse", "downloads"];

// ---------- theme (paper / chalkboard) ----------
// index.html already applied the saved or system theme before first paint.

const systemDark = matchMedia("(prefers-color-scheme: dark)");

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  render($("#theme-toggle"), icon(theme === "dark" ? "sun" : "moon"));
}

function initTheme() {
  setTheme(document.documentElement.dataset.theme);
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    storage.set("theme", next);
    setTheme(next);
  });
  // Follow the system until the user picks a theme themselves.
  systemDark.addEventListener("change", (event) => {
    if (!storage.get("theme")) setTheme(event.matches ? "dark" : "light");
  });
}

// ---------- tabs ----------

function showTab(tab) {
  state.tab = tab;
  $$(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  TABS.forEach((name) => { $(`#tab-${name}`).hidden = name !== tab; });
  if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);

  if (tab === "library") loadLibrary();
  if (tab === "browse" && !state.searched) runSearch();
  if (tab === "downloads") renderDownloads();
}

function initNavigation() {
  $$(".tab").forEach((button) => button.addEventListener("click", () => showTab(button.dataset.tab)));

  // Buttons anywhere can switch tab with data-goto="browse".
  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-goto]");
    if (link) showTab(link.dataset.goto);
  });

  // "/" focuses the search or filter box of the current tab.
  document.addEventListener("keydown", (event) => {
    const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
    if (event.key !== "/" || typing) return;
    const box = { browse: "#search-query", library: "#library-filter" }[state.tab];
    if (box) {
      event.preventDefault();
      $(box).focus();
    }
  });
}

// ---------- start ----------

initTheme();
initOverlays();
initBanner();
initNavigation();
initLibrary();
initBrowse();
initDownloads();
$("#settings-open").addEventListener("click", openSettings);

on("library:changed", loadLibrary);
on("downloads:changed", pollDownloads);
on("status:changed", renderBanner);

const initialTab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "library";
showTab(initialTab);
if (initialTab !== "library") loadLibrary();   // the stats and "on your shelf" tags need it
loadToken();
pollDownloads();

// Poll only while the page is visible; a background tab shouldn't keep the server busy.
setInterval(() => { if (!document.hidden) pollDownloads(); }, POLL_INTERVAL_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollDownloads(); });
