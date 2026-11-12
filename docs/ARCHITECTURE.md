# How Hoard works

Hoard is one Python process (FastAPI + Uvicorn) serving one static HTML page. There's no database: the models folder **is** the state.

```
 Browser (index.html)  ──HTTP/JSON──▶  FastAPI (app/main.py)  ──▶  Hugging Face Hub API
                                          │
                                          ├── spawns one download process per job ──▶ huggingface_hub.snapshot_download
                                          │                                              │
                                          └── reads/writes ─────────────────────────────▶ /models/<org>__<name>/
                                                                                         /config/hf_token
```

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

`get_token()` picks the token in this order:

1. `HF_TOKEN` environment variable (set by the server admin), **then**
2. `/config/hf_token` (saved from the UI).

Every Hub call passes the token explicitly (`HfApi(token=get_token())`, and the download process gets it as an argument through a pipe, not the command line). When saving from the UI:

- Format check (`hf_` prefix, length, no whitespace), then `whoami()` against Hugging Face. Rejected tokens are never stored.
- The file is written to a temp file created with mode `0600`, then atomically renamed into place.
- Status responses only include `hf_…` plus the last four characters, the account name, token name and role.

## Request protection

Two middlewares wrap every request:

1. **Basic auth** (only when `APP_PASSWORD` is set). The password is compared in constant time.
2. **CSRF guard.** `POST`/`PUT`/`DELETE` under `/api/` need `X-Requested-With: hoard`. Browsers won't add a custom header to a cross-site request without a CORS preflight, and Hoard never answers preflights. JSON bodies also need `Content-Type: application/json`, which FastAPI enforces.

Folder names from URLs go through `model_dir()`, which rejects separators, leading dots, and anything that resolves outside `MODELS_DIR`.

## Frontend

`app/static/index.html` holds everything: markup, CSS, inline SVG icons and plain JavaScript. There's no build step or framework.

**Hand-drawn look, technically:**

- **Pencil borders:** the `.sk` class draws its border on `::before` (and a faint second stroke on `::after`) with uneven `border-radius` values. An SVG `feTurbulence` + `feDisplacementMap` filter (`#rough`, `#rough2`) makes the lines wobble. The filter only applies to these pseudo-elements, so text stays sharp.
- **Highlighter:** the active tab and primary buttons use a slightly rotated, filtered yellow block behind the text. Primary buttons layer the yellow fill (`::before`, `z-index:-1`) above a scribbled ink shadow (`::after`, `z-index:-2`).
- **Pencil shading:** progress and disk bars are `repeating-linear-gradient` hatching.
- **Icons:** hand-drawn SVG `<symbol>`s with slightly uneven paths, used through `<use href="#i-name">`.
- **Themes:** colors are CSS variables on `:root`. Dark mode (the chalkboard) applies via `prefers-color-scheme` unless `data-theme` is set by the toggle (saved in `localStorage`).
- **Fonts:** Caveat (headings and numbers), Patrick Hand (body) and Courier Prime (file names), served from `/static/fonts`.

**Data flow:** the page polls `GET /api/downloads` every 1.5 s, which drives the tab badge, the Downloads list, and a library refresh when a job finishes. Speed is smoothed on the client because bytes arrive in bursts. Search is debounced (400 ms), and a sequence counter drops responses that arrive out of order.

## Known limits

- The job list is in memory, so a restart forgets running downloads. Start them again and they resume from the partial files on disk.
- Files removed from a repo upstream aren't deleted locally on update.
- Progress counts files already in the folder, so re-downloading into an existing model can show a high percentage immediately.
- Search lists models only, not datasets or Spaces.
