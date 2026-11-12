# How Hoard works

Hoard is one Python process (FastAPI + Uvicorn) serving a small static web UI. There's no database: the models folder **is** the state.

```
 Browser (app/static)  ──HTTP/JSON──▶  FastAPI (app/)         ──▶  Hugging Face Hub API
                                          │
                                          ├── spawns one download process per job ──▶ huggingface_hub.snapshot_download
                                          │                                              │
                                          └── reads/writes ─────────────────────────────▶ /models/<org>__<name>/
                                                                                         /config/hf_token
```

## Backend

```
app/
├── main.py         assembles the app: middleware, routers, static files, Hub error handler
├── config.py       settings from environment variables (MODELS_DIR, CONFIG_DIR, APP_PASSWORD)
├── hub.py          Hugging Face: which token to use, saving it, the API client, readable errors
├── storage.py      the models folder: folder names, .hoard.json, scanning sizes and formats
├── jobs.py         download jobs: start, progress, stop, the in-memory job list
├── middleware.py   basic auth, CSRF guard, no-cache headers for the UI
└── routes/         the HTTP API (see API.md), one router per area
    ├── browse.py       /api/search, /api/repo
    ├── downloads.py    /api/downloads
    ├── library.py      /api/library
    └── settings.py     /api/settings/token
```

Routes stay thin: they validate input and call `hub`, `storage` or `jobs`. Only `jobs.py` holds state.

## State

| What | Where | Lifetime |
|---|---|---|
| Models | `/models/<org>__<name>/` (plain repo layout) | Until you delete them |
| Per-model notes | `/models/<org>__<name>/.hoard.json` | Written when a download finishes |
| Resume data | `/models/<org>__<name>/.cache/huggingface/` | Managed by `huggingface_hub` |
| Saved token | `/config/hf_token` (mode `0600`) | Until removed in the UI |
| Download jobs | Memory | Until the container restarts |

`.hoard.json` example:

```json
{
  "repo_id": "HuggingFaceTB/SmolLM2-135M-Instruct",
  "revision": "main",
  "sha": "5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be",
  "include": ["config.json", "*.json", "model.safetensors"],
  "license": "apache-2.0",
  "pipeline_tag": "text-generation",
  "library_name": "transformers",
  "gated": false,
  "downloaded_at": "2026-11-12T16:03:44+00:00"
}
```

- `revision` is the branch you asked for. `sha` is the exact commit you have. Update checks compare `sha` with the branch's current commit.
- `include` is the file selection (`null` means the whole repo). Updates and later downloads reuse it, and new selections are merged into it.

The library view is built by scanning the models folder on every request. Any folder not starting with `.` is listed, so models you copy in by hand appear too (marked unmanaged).

## Downloads

1. `POST /api/downloads` fetches the repo's file list and current commit, then filters the files by `include` to work out the expected total size.
2. A **separate process** (`multiprocessing`, `spawn`) runs `snapshot_download(repo, revision=<commit>, local_dir=<folder>, allow_patterns=include)`. Pinning to the commit means every file comes from one revision even if the repo changes mid-download.
3. A monitor thread in the server measures the folder's size (resume data included) about once a second. That's the progress number. `huggingface_hub` writes data in chunks, so progress moves in steps.
4. When the process exits, its result comes back over a queue. On success Hoard writes `.hoard.json`. On failure the error's first line is stored on the job.

**Why a process and not a thread?** So **Stop** is reliable: the server terminates the process. `huggingface_hub` keeps partial files in `.cache/huggingface/download/`, so running the same download again resumes instead of starting over.

**Concurrency:** each repo can have one active job (a second gets `409`). Different repos download in parallel, each with 4 file workers.

## Hugging Face token

`hub.get_token()` picks the token in this order:

1. `HF_TOKEN` environment variable (set by the server admin), **then**
2. `/config/hf_token` (saved from the UI).

Every Hub call passes the token explicitly (`hub.client()`, and the download process gets it as an argument through a pipe, not the command line). When saving from the UI:

- Format check (`hf_` prefix, length, no whitespace), then `whoami()` against Hugging Face. Rejected tokens are never stored.
- The file is written to a temp file created with mode `0600`, then atomically renamed into place.
- Status responses only include `hf_…` plus the last four characters, the account name, token name and role.

## Request protection

Two middlewares wrap every request:

1. **Basic auth** (only when `APP_PASSWORD` is set). The password is compared in constant time.
2. **CSRF guard.** `POST`/`PUT`/`DELETE` under `/api/` need `X-Requested-With: hoard`. Browsers won't add a custom header to a cross-site request without a CORS preflight, and Hoard never answers preflights. JSON bodies also need `Content-Type: application/json`, which FastAPI enforces.

Folder names from URLs go through `storage.model_dir()`, which rejects separators, leading dots, and anything that resolves outside `MODELS_DIR`.

## Frontend

Plain HTML, CSS and native ES modules: no framework, no bundler, no build step. The browser loads the files as they are, and the server sends `Cache-Control: no-cache` for them so an update never mixes old and new modules (unchanged files come back as cheap `304`s).

```
app/static/
├── index.html            page skeleton (header, tab sections, empty overlay containers),
│                         the pencil SVG filters, and a tiny script that sets the theme before first paint
├── css/
│   ├── theme.css         fonts + color tokens; light ("paper") and dark ("chalkboard") defined once each
│   ├── base.css          page, typography, icons, and .sk, the pencil-outline primitive
│   ├── components.css    buttons, inputs, tags/chips, lists, bars, notices, modal/drawer/toasts
│   └── views.css         top bar and per-screen layout, plus small-screen rules
├── icons.svg             hand-drawn icon sprite, used as <use href="/static/icons.svg#i-name">
└── js/
    ├── main.js           entry point: theme toggle, tabs, "/" shortcut, event wiring, polling
    ├── state.js          shared state object + on()/emit() event bus
    ├── lib/
    │   ├── dom.js        $, $$, render(), icon(), and the html`` template tag
    │   ├── api.js        fetch wrapper (adds the CSRF header), query builder, safe localStorage
    │   ├── format.js     sizes, counts, "3d ago", durations, task names
    │   └── models.js     pure file logic: weights, GGUF quantizations, presets, default selection
    ├── ui/
    │   ├── overlay.js    toasts, modal + confirmDialog(), drawer, Escape/backdrop handling
    │   └── components.js avatar, repo name, loading/empty/error states, doodles
    └── views/
        ├── library.js    stats, model list, files, update checks, delete
        ├── browse.js     search with debounce and out-of-order protection
        ├── model-panel.js  facts, notices, and the FilePicker class
        ├── downloads.js  polling, speed smoothing, stop/resume, tab badge
        └── settings.js   Hugging Face token dialog
```

**Conventions:**

- **Templates are escaped by default.** Views build markup with `` html`...` `` from `lib/dom.js`, which escapes every interpolated value (repo names, file paths, errors all come from the network). Nested `` html`` `` results and arrays are inserted as-is, and `null`/`false` render nothing, so optional parts read as `${cond && html`...`}`. `raw()` is only for hand-written markup such as icons.
- **Views don't import each other's internals.** They talk through `state.js` events: `library:changed` (reload the library), `downloads:changed` (poll now). Tab switches use a `data-goto="browse"` attribute handled in `main.js`. The one direct link is browse → model panel → settings (the panel offers "Add token").
- **Logic vs. rendering.** Anything that doesn't touch the DOM (formatting, which files to tick) lives in `lib/` and is easy to test on its own.

**Hand-drawn look, technically:**

- **Pencil borders:** the `.sk` class draws its border on `::before` (and a faint second stroke on `::after`) with uneven `border-radius` values. An SVG `feTurbulence` + `feDisplacementMap` filter (`#rough`, `#rough2`) makes the lines wobble. The filter only applies to these pseudo-elements, so text stays sharp.
- **Highlighter:** the active tab and primary buttons use a slightly rotated, filtered yellow block behind the text. Primary buttons layer the yellow fill (`::before`, `z-index:-1`) above a scribbled ink shadow (`::after`, `z-index:-2`).
- **Pencil shading:** progress and disk bars are `repeating-linear-gradient` hatching.
- **Icons:** hand-drawn SVG `<symbol>`s in `icons.svg` with slightly uneven paths, inserted with `icon("name")`. The pencil filters stay inline in `index.html` because CSS `filter: url(#…)` can only reach filters in the same document.
- **Themes:** colors are CSS variables in `theme.css`. A tiny inline script in `index.html` sets `data-theme` before first paint (saved choice, else the system setting), so each theme is defined once and never flashes. The toggle saves the choice in `localStorage`; until then Hoard follows system changes.
- **Fonts:** Caveat (headings and numbers), Patrick Hand (body) and Courier Prime (file names), served from `/static/fonts`.

**Data flow:** the page polls `GET /api/downloads` every 1.5 s, which drives the tab badge, the Downloads list, and a library refresh when a job finishes. Speed is smoothed on the client because bytes arrive in bursts. Search is debounced (400 ms), and a sequence counter drops responses that arrive out of order.

## Known limits

- The job list is in memory, so a restart forgets running downloads. Start them again and they resume from the partial files on disk.
- Files removed from a repo upstream aren't deleted locally on update.
- Progress counts files already in the folder, so re-downloading into an existing model can show a high percentage immediately.
- Search lists models only, not datasets or Spaces.
