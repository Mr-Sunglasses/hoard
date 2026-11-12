# Hoard HTTP API

The web UI is a client of this API, and you can use it from scripts too. All responses are JSON.

- **Base URL:** `http://<server>:8080`
- **Auth:** when `APP_PASSWORD` is set, every request needs HTTP Basic auth (any username, that password).
- **Changing requests** (`POST`, `PUT`, `DELETE` under `/api/`) must send `X-Requested-With: hoard`. Without it the server returns `403`. This blocks cross-site requests from other web pages.
- **Errors** look like `{"detail": "human readable message"}`. Errors from Hugging Face keep their status code (e.g. `401`/`403` for gated models, `404` for missing repos).

```bash
# example with a password set
curl -u me:secret http://nas:8080/api/library
curl -u me:secret -X POST http://nas:8080/api/downloads \
  -H 'X-Requested-With: hoard' -H 'Content-Type: application/json' \
  -d '{"repo_id": "bartowski/Llama-3.2-3B-Instruct-GGUF", "include": ["*Q4_K_M.gguf"]}'
```

---

## Hugging Face

### `GET /api/search`

Search models on the Hub.

| Query param | Default | Notes |
|---|---|---|
| `q` | `""` | Search text. Empty lists models by `sort`. |
| `sort` | `downloads` | `downloads`, `likes`, `trending_score`, `last_modified`, `created_at` |
| `task` | `""` | Hugging Face pipeline tag, e.g. `text-generation`, `automatic-speech-recognition` |
| `limit` | `30` | 1–100 |

```json
[{ "id": "Qwen/Qwen2.5-7B-Instruct", "downloads": 1234567, "likes": 890,
   "pipeline_tag": "text-generation", "library_name": "transformers",
   "last_modified": "2026-09-01 10:00:00+00:00" }]
```

### `GET /api/repo?repo_id=<org/name>&revision=main`

A repo's details and full file list with sizes.

```json
{ "id": "...", "sha": "5f91d94...", "gated": false, "pipeline_tag": "...", "library_name": "...",
  "license": "apache-2.0", "downloads": 100, "likes": 5,
  "files": [{ "path": "config.json", "size": 662 }],
  "in_library": true }
```

`gated` is `false`, `"auto"` or `"manual"`. `in_library` is true when Hoard already has this repo.

---

## Downloads

### `POST /api/downloads`

Start downloading a repo (or part of it) into the models folder.

```json
{ "repo_id": "org/name", "revision": "main", "include": ["config.json", "*.safetensors"] }
```

- `include`: exact file paths or `*` glob patterns. `null` or missing means the whole repo.
- The branch or tag in `revision` is resolved to a commit when the download starts, so every file comes from the same commit.
- If the repo is already in the library, the new selection is **merged** with the saved one, so later updates fetch both.

Returns the new job (see below). Errors: `400` no files match, `409` this repo is already downloading.

### `GET /api/downloads`

All jobs since the server started, newest first:

```json
[{ "id": "8563f4c3", "repo_id": "org/name", "name": "org__name", "revision": "main",
   "sha": "5f91d94...", "include": ["..."], "file_count": 5,
   "total_bytes": 3413593, "done_bytes": 1200000,
   "status": "downloading", "error": null,
   "started_at": "2026-11-12T16:03:39+00:00", "finished_at": null }]
```

`status` is one of `downloading`, `done`, `cancelled`, `error`. `done_bytes` is sampled about once a second from the disk and moves in steps.

### `DELETE /api/downloads/{id}`

Stop a running download. Partial data is kept, so starting the same download again resumes it.

### `POST /api/downloads/clear`

Remove finished, failed and cancelled jobs from the list.

---

## Library

### `GET /api/library`

```json
{ "models_dir": "/models",
  "disk": { "total": 494384795648, "used": 417989922816, "free": 76394872832 },
  "models": [{
    "name": "HuggingFaceTB__SmolLM2-135M-Instruct",
    "repo_id": "HuggingFaceTB/SmolLM2-135M-Instruct",
    "managed": true, "downloading": false,
    "size": 272629760, "file_count": 12, "formats": ["safetensors"],
    "revision": "main", "sha": "...", "include": ["..."] ,
    "license": "apache-2.0", "pipeline_tag": "text-generation", "library_name": "transformers",
    "gated": false, "downloaded_at": "2026-11-12T16:03:44+00:00" }] }
```

`managed` is false for folders that weren't downloaded through Hoard. Those only have `name`, `repo_id` (guessed from the folder name), `size`, `file_count` and `formats`.

### `GET /api/library/{name}/files`

The files in a model folder (Hoard's metadata and the download cache are left out): `[{ "path": "config.json", "size": 861 }]`.

### `GET /api/library/{name}/check`

Compare the saved commit with the latest on the same branch:
`{ "update_available": true, "latest_sha": "...", "local_sha": "..." }`. Returns `400` for unmanaged folders.

### `POST /api/library/{name}/update`

Download the latest revision with the saved file selection. Returns a job like `POST /api/downloads`.

### `DELETE /api/library/{name}`

Delete the model folder from disk. Returns `409` while it's downloading.

`{name}` is always the folder name (`org__model`). Names containing `/`, `\` or starting with `.` are rejected.

---

## Settings: Hugging Face token

The token itself is **never** returned by any endpoint.

### `GET /api/settings/token`

```json
{ "source": "ui", "ui_writable": true, "protected": true,
  "hint": "hf_…abcd", "user": "your-hf-username", "token_name": "hoard",
  "role": "read", "valid": true }
```

- `source`: `"env"` (from `HF_TOKEN`), `"ui"` (saved from the UI) or `null` (no token).
- `ui_writable`: false when `HF_TOKEN` is set.
- `protected`: whether `APP_PASSWORD` is set.
- `role`: `read`, `write` or `fineGrained`. `valid` is false if Hugging Face rejects the token.

### `PUT /api/settings/token`

`{ "token": "hf_..." }`: checks the token with Hugging Face, then saves it. Returns `{ "ok": true, "user": ..., "token_name": ..., "role": ... }`.
Errors: `400` malformed or rejected token, `409` `HF_TOKEN` is set, `500` the config folder isn't writable.

### `DELETE /api/settings/token`

Remove the saved token. Returns `409` when the token comes from `HF_TOKEN`.
