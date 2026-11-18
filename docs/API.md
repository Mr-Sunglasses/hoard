# Hoard API

The web UI uses this JSON API, and scripts can too.

| Endpoint | What it does |
|---|---|
| `GET /api/search` | Search models on Hugging Face |
| `GET /api/repo` | One repo's details and files |
| `POST /api/downloads` | Start a download |
| `GET /api/downloads` | List downloads |
| `DELETE /api/downloads/{id}` | Stop a download |
| `POST /api/downloads/clear` | Remove finished downloads from the list |
| `GET /api/library` | Models on disk, plus disk space |
| `GET /api/library/{name}/files` | Files in one model |
| `GET /api/library/{name}/check` | Is a newer revision available? |
| `POST /api/library/{name}/update` | Download the newer revision, or finish an unfinished download |
| `POST` · `GET /api/library/{name}/verify` | Check the files against Hugging Face's hashes |
| `POST /api/library/{name}/repair` | Download the files the check found damaged again |
| `DELETE /api/library/{name}` | Delete a model from disk |
| `GET` · `PUT` · `DELETE /api/settings/token` | Hugging Face token |
| `GET /healthz` | Health check, needs no password |

**Rules for every request:**

- With `APP_PASSWORD` set, use HTTP Basic auth (any username). Ten wrong passwords lock that address out for 15 minutes (`429`). Behind a reverse proxy, see `TRUSTED_PROXIES`.
- The `Host` header must be an IP, a local name, or listed in `ALLOWED_HOSTS`, or the server answers `421`.
- `POST`, `PUT` and `DELETE` need the header `X-Requested-With: hoard`, or the server answers `403`.
- Errors look like `{"detail": "message"}`. Errors from Hugging Face keep their status code, such as `401` for a gated model or `404` for a missing one.
- `{name}` is the model's folder name, like `org__model`.

```bash
curl -u me:secret -X POST http://nas:8080/api/downloads \
  -H 'X-Requested-With: hoard' -H 'Content-Type: application/json' \
  -d '{"repo_id": "bartowski/Llama-3.2-3B-Instruct-GGUF", "include": ["*Q4_K_M.gguf"]}'
```

## Browse

**`GET /api/search`**: `q` (text), `sort` (`downloads`, `likes`, `trending_score`, `last_modified`, `created_at`), `task` (a pipeline tag like `text-generation`), `limit` (1–100, default 30).

```json
[{ "id": "Qwen/Qwen2.5-7B-Instruct", "downloads": 1234567, "likes": 890,
   "pipeline_tag": "text-generation", "library_name": "transformers",
   "last_modified": "2026-09-01 10:00:00+00:00" }]
```

**`GET /api/repo?repo_id=org/name&revision=main`**

```json
{ "id": "org/name", "sha": "5f91d94...", "gated": false, "license": "apache-2.0",
  "pipeline_tag": "text-generation", "library_name": "transformers",
  "downloads": 100, "likes": 5, "in_library": true,
  "files": [{ "path": "pytorch_model.bin", "size": 662, "pickle": true }],
  "security": { "scanned": true, "issues": [{ "path": "training_args.bin", "level": "caution" }] } }
```

`gated` is `false`, `"auto"` or `"manual"`. `pickle` marks files that can run code when loaded with `torch.load()`. `security` is Hugging Face's own scan of the repo.

## Downloads

**`POST /api/downloads`**

```json
{ "repo_id": "org/name", "revision": "main", "include": ["config.json", "*.safetensors"] }
```

`include` takes exact paths or `*` patterns. Leave it out for the whole repo. If the model is already on disk, the selection is merged with the saved one. Returns the new job. Errors: `400` if no files match, `409` if this repo is already downloading, its files are being checked, or its folder holds a different repo, `507` if it would leave less than `MIN_FREE_GB` free (counting other active downloads).

**`GET /api/downloads`** lists jobs since the server started, newest first:

```json
[{ "id": "8563f4c3", "repo_id": "org/name", "name": "org__name",
   "revision": "main", "sha": "5f91d94...", "include": ["..."],
   "file_count": 5, "total_bytes": 3413593, "needed_bytes": 3413593, "done_bytes": 1200000,
   "status": "downloading", "attempt": 1, "retry_at": null, "error": null,
   "started_at": "2026-11-12T16:03:39+00:00", "finished_at": null }]
```

`status` is `queued`, `downloading`, `retrying`, `done`, `cancelled` or `error`. While `retrying`, `error` holds the reason and `retry_at` the time of the next try. `needed_bytes` is what wasn't on disk yet. The list survives restarts.

**`DELETE /api/downloads/{id}`** stops or unqueues a download. Starting it again keeps every finished file. **`POST /api/downloads/clear`** removes every job that isn't running.

## Library

**`GET /api/library`**

```json
{ "models_dir": "/models",
  "disk": { "total": 494384795648, "used": 417989922816, "free": 76394872832, "reserve": 2147483648 },
  "problems": [],
  "models": [{ "name": "org__name", "repo_id": "org/name",
               "managed": true, "incomplete": false, "downloading": false, "pickle": false,
               "size": 272629760, "file_count": 12, "formats": ["safetensors"],
               "...": "plus every field from .hoard.json" }] }
```

- `managed` is false for folders added by hand. Those have only the disk fields.
- `incomplete` means the last download didn't finish. Call `update` to finish it.
- `problems` lists setup errors, such as a folder the server can't write to.
- Summaries are cached for 5 minutes to avoid waking sleeping disks; add `?refresh=true` to rescan now.

- **`GET /api/library/{name}/files`**: `[{ "path": "config.json", "size": 861 }]`
- **`GET /api/library/{name}/check`**: `{ "update_available": true, "latest_sha": "...", "local_sha": "..." }`
- **`POST /api/library/{name}/update`**: starts a download with the saved selection and returns the job.
- **`POST /api/library/{name}/verify`** starts a check in the background; poll **`GET`** for its progress:
  `{ "status": "running", "checked_bytes": 1048576, "total_bytes": 723675601, "file_count": 2, "problems": [], "error": null }`.
  `status` goes `waiting` → `running` → `done` or `error`. Each problem is `{ "path": "...", "issue": "missing" | "wrong size" | "changed or corrupted" }`. Checks run one at a time. `POST` returns `409` while the model is downloading or already being checked; `GET` returns `404` until a check has run, and again after the model's files change.
- **`POST /api/library/{name}/repair`**: deletes the files the last check flagged and downloads them again from the same commit. Nothing is deleted if the download is refused. Returns the job.
- **`DELETE /api/library/{name}`**: `409` while the model is downloading, queued or being checked.

`check`, `update`, `verify` and `repair` return `400` for folders added by hand.

## Token

No endpoint ever returns the token itself.

**`GET /api/settings/token`**

```json
{ "source": "ui", "ui_writable": true, "protected": true,
  "hint": "hf_…abcd", "user": "your-hf-username", "token_name": "hoard",
  "role": "read", "valid": true }
```

- `source`: `"env"` (from `HF_TOKEN`), `"ui"` (saved in the UI) or `null`.
- `ui_writable`: false when `HF_TOKEN` is set.
- `protected`: whether `APP_PASSWORD` is set.

**`PUT`** with `{ "token": "hf_..." }` checks the token with Hugging Face, saves it, and returns the account details. **`DELETE`** removes it. Both return `409` when `HF_TOKEN` is set; `PUT` returns `400` for a bad token.
