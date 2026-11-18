// Loaded as a plain blocking script in <head>: applies the theme before first paint
// (the saved choice, else the system setting), so the page never flashes the wrong colors.
(() => {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch {}
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = saved || (dark ? "dark" : "light");
})();
