# How Hoard works

Hoard is one Python process (FastAPI + Uvicorn) serving a static web UI. There's no database: the models folder is the state.

```
Browser ──JSON──▶ FastAPI ──▶ Hugging Face API
                     │
                     ├──▶ one process per download ──▶ snapshot_download ──▶ /models/<org>__<name>/
                     └──▶ reads and writes ─────────────────────────────────▶ /models, /config/{hf_token,jobs.json}
```

## Code map

```
app/
├── main.py           assembles the app: middleware, routers, static files, error handler
├── config.py         settings from environment variables
├── hub.py            Hugging Face: which token to use, the API client, readable errors
├── storage.py        the models folder: folder names, .hoard.json, sizes, disk space
├── jobs.py           download jobs: queue, processes, retries, saved to /config/jobs.json
├── verify.py         checking saved files against Hugging Face's hashes
├── middleware.py     security headers, host check, password, CSRF guard, cache headers
├── routes/           the HTTP API, one router per area: browse, downloads, library, settings
└── static/
    ├── index.html    page skeleton, pencil SVG filters, theme set before first paint
    ├── icons.svg     hand-drawn icon sprite
    ├── css/          theme (colors, fonts) · base (pencil outline) · components · views
    └── js/
        ├── theme-init.js  sets the theme before first paint (a file, since the CSP forbids inline scripts)
        ├── main.js   entry point: theme, tabs, shortcuts, polling
        ├── state.js  shared state and a tiny on()/emit() event bus
        ├── lib/      no DOM: html`` templates, API client, formatting, file-selection rules
        ├── ui/       modal, drawer, toasts, warning banner, shared components
        └── views/    library, browse, model panel, downloads, settings
```

Routes stay thin: they check input and call `hub`, `storage`, `jobs` or `verify`. Tests live in `tests/` and never touch the network.

**CI** (`.github/workflows/ci.yml`) runs on every push and pull request: ruff, the tests, a syntax check of the JavaScript, a vulnerability audit of the locked dependencies, and a start and stop of the hardened container. **Releases** (`release.yml`) run on a `v*` tag: the same checks, then a multi-arch image pushed to GHCR with provenance and an SBOM, and a GitHub release from `CHANGELOG.md`. See [Contributing](../CONTRIBUTING.md#releasing-maintainers).

## Run it locally

Install [uv](https://docs.astral.sh/uv/); it fetches Python 3.12 for you.

```bash
uv sync
mkdir -p models config
MODELS_DIR=$PWD/models CONFIG_DIR=$PWD/config HF_HOME=$PWD/models/.hf-home \
  uv run uvicorn app.main:app --reload --port 8080
```

Run the tests with `uv run pytest`. There's no frontend build: edit a file under `app/static/` and reload the page. Use `uv add <package>` to add a dependency and `uv lock --upgrade` to update them all. The Docker image installs from `uv.lock`.

## The models folder

Each repo is saved to `/models/<org>__<name>/` with its own file layout, plus two extras:

- `.hoard.json`: what Hoard downloaded. While a download runs, or after one fails, it has `"incomplete": true`, so the model shows as *unfinished* instead of looking healthy.
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

```
queued ──▶ downloading ──▶ done
              │  ▲
              ▼  │   network trouble: retried after 15 s, 1 min, 5 min
           retrying
              │
              ▼
       error / cancelled
```

1. `jobs.start()` fetches the file list and current commit, works out what isn't on disk yet, and refuses (`507`) if that plus the other active downloads would leave less than `MIN_FREE_GB`. Picking more files for a model you already have adds them to its selection, and the whole selection is counted, since files that changed upstream are fetched again too. The job joins the queue.
2. At most `MAX_PARALLEL_DOWNLOADS` run at once, oldest first. When one starts, `.hoard.json` is marked incomplete.
3. A separate process runs `snapshot_download` pinned to the commit, so every file comes from the same revision. A thread measures the folder about once a second for progress (the selected files already in place, plus whatever the folder has grown by), and stops the download if free space falls below half of `MIN_FREE_GB`.
4. Errors are sorted into worth retrying (network, 5xx, 429) and not (gated, not found, disk full, permissions). Only the first kind is retried.
5. On success `.hoard.json` gets the new commit and the incomplete mark goes away.

Each download gets its own process so **Stop** can simply terminate it. The job list is saved to `/config/jobs.json` on every status change, and at startup unfinished jobs are queued again, except ones that had started and whose folder has since been deleted.

**Shutdown:** on `docker stop`, Uvicorn waits at most 5 seconds for open requests, then `jobs.shutdown()` saves the list, freezes it, and terminates the download processes. Freezing matters: each download thread sees its process die and would otherwise record a failure, which wouldn't be continued. The whole stop takes well under Docker's 10-second limit.

**Resuming:** `huggingface_hub` skips files that are already complete, but with Hugging Face's Xet storage it gives every attempt a fresh temp file, so a partly downloaded file starts over. Hoard deletes those leftover temp files before each attempt, when a job ends, and at startup, so they never pile up.

## Verifying files

`verify.py` re-reads every selected file and compares it with what Hugging Face publishes for the exact commit in `.hoard.json`: SHA-256 for large (LFS) files, the git blob hash for small ones. It runs in a background thread, one model at a time to go easy on the disks, and the UI polls for progress. **Repair** downloads the flagged files again from the same commit. It deletes them, plus `huggingface_hub`'s note that they were complete, only once the download has passed every check (space, not already downloading, Hugging Face reachable), so a refused repair leaves the model as it was. A check and a download never run on the same model at once, and starting a download clears the model's earlier check result.

## Token

`hub.get_token()` uses `HF_TOKEN` from the environment first, then `/config/hf_token` saved from the UI. Every Hugging Face call passes it explicitly. The download process gets it through a pipe, never on the command line.

A token saved from the UI is checked for format, then with Hugging Face (`whoami`), and rejected tokens are never stored. It's written to a `0600` temp file and renamed into place. The API only ever returns its last four characters, the account, and the token's name and role.

## Request protection

Middlewares run in this order on every request:

1. **Security headers:** a Content Security Policy that allows scripts, styles, fonts and requests only from Hoard itself (inline styles are allowed for computed widths and colors; inline scripts are not), `frame-ancestors 'none'` against clickjacking, `nosniff`, and `no-referrer`.
2. **Host check:** the `Host` header must be an IP address, `localhost`, a single-word name, a local suffix (`.local`, `.lan`, `.home.arpa`, `.internal`…), or match `ALLOWED_HOSTS`. Otherwise the answer is `421`. This blocks DNS rebinding, where a website points its own domain at your server to reach Hoard from inside your browser.
3. **Password:** with `APP_PASSWORD` set, every request except `/healthz` needs HTTP Basic auth. The comparison takes the same time whether the password is right or wrong. After 10 wrong passwords in 15 minutes, that address gets `429` until the window passes. Behind a reverse proxy every request comes from the proxy's address, so list the proxy in `TRUSTED_PROXIES`: for requests from it, the address is the last `X-Forwarded-For` entry that isn't a trusted proxy. Headers from anyone else are ignored, so they can't dodge the lockout with made-up addresses.
4. **CSRF:** `POST`/`PUT`/`DELETE` under `/api/` need `X-Requested-With: hoard`. Browsers can't add that header to a cross-site request without a CORS preflight, which Hoard never answers.
5. **Cache headers:** `no-cache` for the UI, `no-store` for API responses.

Folder names from URLs go through `storage.model_dir()`, and file paths from Hugging Face through `storage.inside()`, so nothing can reach outside `MODELS_DIR`. The interactive API docs (`/docs`) are turned off.

## Container

The image runs as user 1000 by default; the compose file sets your own `PUID:PGID`. The compose file also sets these protections:

- **Read-only filesystem:** only `/models`, `/config` and a `/tmp` in memory are writable.
- **No privileges:** all Linux capabilities dropped, `no-new-privileges`, and a process limit.
- **Process reaping:** `init: true` cleans up finished download processes.
- **Log rotation:** 3 × 10 MB. Successful polling isn't logged at all.
- **Health check:** Docker's health check calls `/healthz`.

Base images are pinned by digest. Dependabot proposes updates for them, the Python packages and the CI actions.

If a folder isn't writable, Hoard still starts: it logs how to fix it and shows the problem in the UI.

## Frontend

Plain HTML, CSS and ES modules, with no framework and no bundler, and no inline scripts (the CSP forbids them). The server sends `Cache-Control: no-cache`, so after an update the browser never mixes old and new modules. Unchanged files come back as `304`s.

- **Escaped templates.** Views build markup with `` html`…` `` from `lib/dom.js`, which escapes every value. Nested templates and arrays go in as-is, and `null`/`false` render nothing, so optional parts read `${cond && html`…`}`. Use `raw()` only for trusted markup like icons.
- **Views stay separate.** They talk through events on `state.js` (`library:changed`, `downloads:changed`), and buttons switch tabs with `data-goto="browse"`.
- **Logic apart from DOM.** Formatting and file-selection rules live in `lib/`.
- **Data flow.** `GET /api/downloads` is polled every 1.5 s while the page is visible, and drives the badge, the list and library refreshes. Search waits 400 ms after typing and ignores responses that arrive out of order.

**The hand-drawn look:**

- **Pencil outlines:** `.sk` draws its border on `::before`/`::after` with uneven radii. An SVG turbulence filter (`#rough`, inline in `index.html` because CSS can only reference filters in the same page) makes it wobble without blurring text.
- **Highlighter:** a slightly rotated yellow block behind the active tab and primary buttons.
- **Shading:** progress bars use `repeating-linear-gradient` hatching.
- **Themes:** CSS variables in `theme.css`. `theme-init.js`, loaded before the stylesheets, sets `data-theme` before first paint, so there's no flash.

## Known limits

- A partly downloaded file starts over after Stop or a restart (finished files are kept).
- Files deleted from a repo upstream stay on disk after an update.
- Library summaries are cached for 5 minutes, so changes made outside Hoard can take that long to show up (`GET /api/library?refresh=true` rescans at once). Whether the folders are writable is tested by writing a file, so once that passes it isn't tested again until a restart.
- Search covers models only, not datasets or Spaces.
