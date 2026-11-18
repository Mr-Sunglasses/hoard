# Contributing to Hoard

Thanks for helping! Bug reports, fixes, docs and ideas are all welcome.

## Reporting bugs and asking for features

Open an issue with what you did, what you expected and what happened instead. Include the Hoard version (`docker compose logs hoard | grep "Hoard "`) and anything relevant from the log. For security problems, see [SECURITY.md](SECURITY.md) instead.

For a bigger change, please open an issue first to talk it through, so your time isn't wasted on something that doesn't fit.

## Setting up

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you) and, to check the JavaScript, Node.js.

```bash
git clone https://github.com/Mr-Sunglasses/hoard.git && cd hoard
uv sync
mkdir -p models config
MODELS_DIR=$PWD/models CONFIG_DIR=$PWD/config HF_HOME=$PWD/models/.hf-home \
  uv run uvicorn app.main:app --reload --port 8080
```

Open <http://localhost:8080>. Python changes reload by themselves; for the UI, edit a file under `app/static/` and reload the page. There's no build step.

[How it works](docs/ARCHITECTURE.md) explains the code layout and design.

## Before you open a pull request

Run what CI runs:

```bash
uv run pytest -q                                                        # tests
uvx ruff@0.14.5 check .                                                 # lint
find app/static/js -name '*.js' -print0 | xargs -0 -n1 node --check     # JavaScript parses
docker build -t hoard .                                                 # the image builds
```

And check that:

- New behaviour has a test. Tests never touch the network: fake Hugging Face with `monkeypatch` (see `fake_hub` in `tests/test_jobs.py`).
- Docs match: the README for anything a user sees, `docs/API.md` for API changes, `docs/ARCHITECTURE.md` for design changes, and an entry under **Unreleased** in `CHANGELOG.md`.
- A new setting is in `app/config.py`, `.env.example`, `docker-compose.yml` and the settings tables in the README and `docs/DEPLOYMENT.md`.

## Style

- **Plain words.** UI text, errors and docs are written for someone running a NAS, not a developer: short sentences, say what happened and what to do about it.
- **Few moving parts.** No database, no frontend framework, no bundler. A new dependency needs a good reason.
- **Python:** type hints on public functions, docstrings that explain *why*, and modules that match the code map in `docs/ARCHITECTURE.md`. Routes stay thin and call `hub`, `storage`, `jobs` or `verify`.
- **JavaScript:** ES modules, and every bit of HTML built with the `html` template tag from `lib/dom.js`, which escapes values. Never put API data in `raw()`.
- **Security:** keep the Content Security Policy strict (no inline scripts, nothing from other origins), and send every path through `storage.model_dir()` or `storage.inside()`.

## Releasing (maintainers)

1. Move the **Unreleased** notes in `CHANGELOG.md` under a new version heading, and set the same version in `pyproject.toml`. Run `uv lock`.
2. Commit, then tag and push: `git tag v1.2.0 && git push origin main v1.2.0`.
3. The release workflow runs every check, publishes `ghcr.io/mr-sunglasses/hoard` for amd64 and arm64 (tags `1.2.0`, `1.2`, `1`, `latest`), and creates the GitHub release from the changelog.

Versions follow [semantic versioning](https://semver.org/): a new major version means you have to do something when updating, and the changelog says what.
