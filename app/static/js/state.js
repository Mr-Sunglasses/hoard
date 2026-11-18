// Shared app state and a tiny event bus so views don't import each other.

export const state = {
  tab: "library",

  // library
  library: [],          // models from /api/library
  disk: null,           // { total, used, free }
  modelsDir: "",
  libraryLoaded: false,
  updates: {},          // folder name -> { checking } | { error } | /check response
  verifies: {},         // folder name -> /verify response
  problems: [],         // server setup problems (unwritable folders)

  // downloads
  jobs: [],             // from /api/downloads
  speeds: {},           // job id -> { bytes, time, rate }

  // browse
  searched: false,

  // settings
  token: null,          // /api/settings/token response
};

/*
 * Events used across views:
 *   "library:changed"   something on disk changed; reload the library
 *   "downloads:changed" a download was started/stopped; poll now
 *   "status:changed"    token or setup problems changed; redraw the warning banner
 */
const listeners = {};

export function on(event, handler) {
  (listeners[event] ||= []).push(handler);
}

export function emit(event, detail) {
  (listeners[event] || []).forEach((handler) => handler(detail));
}
