# How Hoard works

Hoard is one Python process (FastAPI + Uvicorn) serving a static web UI. There's no database: the models folder is the state.

```
Browser ──JSON──▶ FastAPI ──▶ Hugging Face API
                     │
                     ├──▶ one process per download ──▶ snapshot_download ──▶ /models/<org>__<name>/
                     └──▶ reads and writes ─────────────────────────────────▶ /models, /config/hf_token
```

## Code map

```
app/
├── main.py           assembles the app: middleware, routers, static files, error handler
├── config.py         settings from environment variables
├── hub.py            Hugging Face: which token to use, the API client, readable errors
├── storage.py        the models folder: folder names, .hoard.json, sizes and formats
├── jobs.py           download jobs: start, progress, stop (the only in-memory state)
├── middleware.py     basic auth, CSRF guard, no-cache headers
├── routes/           the HTTP API, one router per area: browse, downloads, library, settings
└── static/
    ├── index.html    page skeleton, pencil SVG filters, theme set before first paint
    ├── icons.svg     hand-drawn icon sprite
    ├── css/          theme (colors, fonts) · base (pencil outline) · components · views
    └── js/
        ├── main.js   entry point: theme, tabs, shortcuts, polling
        ├── state.js  shared state and a tiny on()/emit() event bus
        ├── lib/      no DOM: html`` templates, API client, formatting, file-selection rules
        ├── ui/       modal, drawer, toasts, shared components
        └── views/    library, browse, model panel, downloads, settings
```

Routes stay thin: they check input and call `hub`, `storage` or `jobs`.

## Run it locally

Install [uv](https://docs.astral.sh/uv/); it fetches Python 3.12 for you.

```bash
uv sync
mkdir -p models config
MODELS_DIR=$PWD/models CONFIG_DIR=$PWD/config HF_HOME=$PWD/models/.hf-home \
  uv run uvicorn app.main:app --reload --port 8080
```

There's no frontend build: edit a file under `app/static/` and reload the page. Use `uv add <package>` to add a dependency and `uv lock --upgrade` to update them all. The Docker image installs from `uv.lock`.

## The models folder

Each repo is saved to `/models/<org>__<name>/` with its own file layout, plus two extras:

- `.hoard.json`: what Hoard downloaded. Written when a download finishes.
- `.cache/huggingface/`: resume data, managed by `huggingface_hub`.

```json
{
  "repo_id": "HuggingFaceTB/SmolLM2-135M-Instruct",
  "revision": "main",
  "sha": "5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be",
  "include": ["*.json", "model.safetensors"],
  "license": "apache-2.0",
  "pipeline_tag": "text-generation",
  "library_name": "transformers",
  "gated": false,
  "downloaded_at": "2026-11-12T16:03:44+00:00"
}
```

- `revision` is the branch you asked for; `sha` is the commit you have. Update checks compare the two.
- `include` is the file selection (`null` means the whole repo). Downloading more files into a model merges them into it, so updates refresh everything.

The library is a fresh scan of the folder on every request. Folders without `.hoard.json` are listed as *added by hand*. Hidden folders (like `.hf-home`, the Hugging Face cache) are skipped.

## Downloads

1. `jobs.start()` fetches the repo's file list and current commit, and adds up the size of the selected files.
2. A separate process runs `snapshot_download` pinned to that commit, so every file comes from the same revision even if the repo changes mid-download.
3. A thread measures the folder about once a second. That's the progress.
4. When the process exits, success writes `.hoard.json`; failure stores the error's first line on the job.

Each download gets its own process so **Stop** can simply terminate it. Partial files stay in `.cache/`, so starting again resumes. One job per repo at a time (a second gets `409`); different repos download in parallel with 4 file workers each.

## Token

`hub.get_token()` uses `HF_TOKEN` from the environment first, then `/config/hf_token` saved from the UI. Every Hugging Face call passes it explicitly. The download process gets it through a pipe, never on the command line.

A token saved from the UI is checked for format, then with Hugging Face (`whoami`), and rejected tokens are never stored. It's written to a `0600` temp file and renamed into place. The API only ever returns its last four characters, the account, and the token's name and role.

## Request protection

- **Password:** with `APP_PASSWORD` set, every request needs HTTP Basic auth. The check takes the same time whether the password is right or wrong.
- **CSRF:** `POST`/`PUT`/`DELETE` under `/api/` need `X-Requested-With: hoard`. Browsers can't add that header to a cross-site request without a CORS preflight, which Hoard never answers.
- **Paths:** folder names from URLs go through `storage.model_dir()`, which rejects anything that could leave `MODELS_DIR`.

## Frontend

Plain HTML, CSS and ES modules, with no framework and no bundler. The server sends `Cache-Control: no-cache`, so after an update the browser never mixes old and new modules. Unchanged files come back as `304`s.

- **Escaped templates.** Views build markup with `` html`…` `` from `lib/dom.js`, which escapes every value. Nested templates and arrays go in as-is, and `null`/`false` render nothing, so optional parts read `${cond && html`…`}`. Use `raw()` only for trusted markup like icons.
- **Views stay separate.** They talk through events on `state.js` (`library:changed`, `downloads:changed`), and buttons switch tabs with `data-goto="browse"`.
- **Logic apart from DOM.** Formatting and file-selection rules live in `lib/`.
- **Data flow.** `GET /api/downloads` is polled every 1.5 s and drives the badge, the list and library refreshes. Search waits 400 ms after typing and ignores responses that arrive out of order.

**The hand-drawn look:**

- **Pencil outlines:** `.sk` draws its border on `::before`/`::after` with uneven radii. An SVG turbulence filter (`#rough`, inline in `index.html` because CSS can only reference filters in the same page) makes it wobble without blurring text.
- **Highlighter:** a slightly rotated yellow block behind the active tab and primary buttons.
- **Shading:** progress bars use `repeating-linear-gradient` hatching.
- **Themes:** CSS variables in `theme.css`. An inline script sets `data-theme` before first paint, so there's no flash.

## Known limits

- The job list lives in memory; after a restart, start a download again and it resumes from disk.
- Files deleted from a repo upstream stay on disk after an update.
- Downloading into an existing model can show a high percentage right away, because existing files count.
- Search covers models only, not datasets or Spaces.
