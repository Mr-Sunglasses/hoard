// Formatting helpers: sizes, counts, relative times, repo ids, task names.

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"];

/** 548105171 -> "522.7 MB" */
export function bytes(n) {
  if (!n) return "0 B";
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), BYTE_UNITS.length - 1);
  return (n / 1024 ** i).toFixed(i > 1 ? 1 : 0) + " " + BYTE_UNITS[i];
}

/** 1048413 -> "1M", 4610 -> "4.6k" */
export function count(n) {
  if (n == null) return "–";
  const short = (value, suffix) => value.toFixed(1).replace(/\.0$/, "") + suffix;
  if (n >= 1e6) return short(n / 1e6, "M");
  if (n >= 1e3) return short(n / 1e3, "k");
  return String(n);
}

const AGO_STEPS = [[31536000, "y"], [2592000, "mo"], [86400, "d"], [3600, "h"], [60, "m"]];

/** ISO date -> "3d ago" */
export function ago(iso) {
  if (!iso) return "–";
  const seconds = (Date.now() - new Date(iso)) / 1000;
  for (const [size, unit] of AGO_STEPS) {
    if (seconds >= size) return `${Math.floor(seconds / size)}${unit} ago`;
  }
  return "just now";
}

/** seconds -> "4m 10s" ("" when unknown) */
export function duration(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

/** "org/name" -> ["org", "name"];  "name" -> ["", "name"] */
export function splitRepoId(id) {
  const slash = id.indexOf("/");
  return slash < 0 ? ["", id] : [id.slice(0, slash), id.slice(slash + 1)];
}

const TASK_NAMES = {
  "text-generation": "Text generation",
  "image-text-to-text": "Vision + text",
  "automatic-speech-recognition": "Speech recognition",
  "text-to-speech": "Text to speech",
  "text-to-image": "Text to image",
  "feature-extraction": "Embeddings",
  "sentence-similarity": "Sentence similarity",
};

/** Hugging Face pipeline tag -> readable name */
export const taskName = (tag) => TASK_NAMES[tag] || (tag || "").replace(/-/g, " ");
