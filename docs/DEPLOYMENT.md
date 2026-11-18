# Deploying Hoard

This guide covers running Hoard for real: installing, putting it behind a reverse proxy, updating, backing up and keeping an eye on it. For a first try, the [quick start](../README.md#quick-start) is enough.

- [What you need](#what-you-need)
- [Install](#install)
- [Folders and permissions](#folders-and-permissions)
- [Reaching Hoard from other devices](#reaching-hoard-from-other-devices)
- [Behind a reverse proxy](#behind-a-reverse-proxy)
- [Updating and rolling back](#updating-and-rolling-back)
- [Backups](#backups)
- [Monitoring](#monitoring)
- [Running without Docker](#running-without-docker)
- [Settings reference](#settings-reference)

## What you need

- A Linux machine with Docker and Docker Compose v2: a home server, a NAS, or a Raspberry Pi 4/5. Images are published for `amd64` and `arm64`.
- Little memory and CPU: Hoard is one small Python server, plus one Python process per running download (`MAX_PARALLEL_DOWNLOADS`, 2 by default).
- Disk space for your models. Hoard writes them as plain files, so any local disk or NAS volume works. Network shares (NFS, SMB) work too, but hashing files with **Check files** is slower over the network.

## Install

There are two ways to get the image. Both use the same `docker-compose.yml` and `.env`.

### Option A: the published image (recommended)

Each release is published to `ghcr.io/mr-sunglasses/hoard` with build provenance and an SBOM.

```bash
mkdir hoard && cd hoard
curl -fsSLO https://raw.githubusercontent.com/Mr-Sunglasses/hoard/main/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/Mr-Sunglasses/hoard/main/.env.example -o .env
```

In `docker-compose.yml`, replace `build: .` with the `image:` line just below it:

```yaml
    image: ghcr.io/mr-sunglasses/hoard:1     # newest 1.x release
```

| Tag | Follows |
|---|---|
| `1.2.3` | exactly that release, never changes |
| `1.2` | bug fixes of 1.2 |
| `1` | every 1.x release (no breaking changes) |
| `latest` | the newest release |

Pick `1` to get fixes automatically on `docker compose pull`, or an exact version to update only when you choose.

To check that an image was built by this repository's release workflow:

```bash
gh attestation verify oci://ghcr.io/mr-sunglasses/hoard:1 --repo Mr-Sunglasses/hoard
```

### Option B: build from source

```bash
git clone https://github.com/Mr-Sunglasses/hoard.git && cd hoard
cp .env.example .env
```

Build from a release tag (`git checkout v1.0.0`) rather than `main` if you want a tested version.

### Then, for both

```bash
sudo mkdir -p /srv/models && sudo chown $(id -u):$(id -g) /srv/models
mkdir -p config && chmod 700 config
```

Edit `.env`: set `PUID`/`PGID` to the output of `id -u` and `id -g`, and set `APP_PASSWORD`. Then:

```bash
docker compose up -d          # option A
docker compose up -d --build  # option B
docker compose logs -f hoard  # look for "Application startup complete"
```

Open `http://<server-ip>:8080`.

## Folders and permissions

Hoard runs as the user in `PUID:PGID`, never as root, so the files it writes belong to you.

| In the container | On your server | Holds |
|---|---|---|
| `/models` | `/srv/models` (the first `volumes:` entry) | your models, one folder per repo |
| `/config` | `./config` | the token saved in the UI and the download list |

To store models somewhere else, change the left side of the first volume, for example `/volume1/models:/models` on a Synology NAS or `/mnt/user/models:/models` on Unraid. The folder must exist and be writable by `PUID:PGID` **before** the first start; otherwise Docker creates it owned by root, and Hoard shows how to fix it on the page and in the log.

`config/` holds a Hugging Face token, so keep it out of shared folders and backups that others can read. `chmod 700 config` limits it to you.

Everything else in the container is read-only, with no Linux capabilities and no way to gain privileges.

## Reaching Hoard from other devices

Hoard has no HTTPS of its own, so the password travels in plain text. That's fine on a home network you trust. For anything more:

- **VPN (simplest):** with [Tailscale](https://tailscale.com/) or WireGuard on the server, open `http://<tailscale-name>:8080` from anywhere. Single-word names and IPs work without any setting; for a MagicDNS name like `nas.tailnet-name.ts.net`, add it to `ALLOWED_HOSTS`.
- **Reverse proxy with HTTPS:** see the next section.
- **Never** forward port 8080 on your router to the internet.

On Ubuntu, Docker's published ports bypass `ufw`. To limit who can connect, set `BIND_ADDRESS` to one of the server's own addresses: its LAN IP, its Tailscale IP, or `127.0.0.1` when a reverse proxy on the same machine is the only way in.

## Behind a reverse proxy

A reverse proxy gives Hoard HTTPS and a domain name. Three settings matter:

1. **`ALLOWED_HOSTS`**: the domain, e.g. `hoard.example.com`. Hoard refuses requests for host names it doesn't know (that's what blocks DNS rebinding attacks), and answers `421` until you add it.
2. **`BIND_ADDRESS=127.0.0.1`** when the proxy runs on the same machine, so nobody can go around it.
3. **`TRUSTED_PROXIES`**: the proxy's address *as Hoard sees it*. Without it, every request seems to come from the proxy, and ten wrong passwords from anyone lock everyone out for 15 minutes. With it, Hoard reads each visitor's address from `X-Forwarded-For`.

What address does Hoard see? A proxy on the Docker host reaches the container through the Docker network's gateway:

```bash
docker network inspect hoard_default -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}'   # e.g. 172.18.0.1
```

`172.16.0.0/12` covers every network Docker creates by default. Only list addresses that nothing but your proxy can connect from: whoever connects from a trusted address can claim to be any visitor.

### Caddy

Caddy gets HTTPS certificates automatically and sends the right headers by default.

```
hoard.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
# .env
ALLOWED_HOSTS=hoard.example.com
BIND_ADDRESS=127.0.0.1
TRUSTED_PROXIES=172.16.0.0/12
```

### nginx

```nginx
server {
    listen 443 ssl;
    server_name hoard.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Use the same `.env` as for Caddy. `proxy_set_header Host $host` matters: without it nginx sends `127.0.0.1`, which works but skips the host check.

### Traefik (in Docker)

When Traefik runs in a container, it reaches Hoard over a shared Docker network instead of a published port. Remove the `ports:` section from `docker-compose.yml` and add:

```yaml
    networks: [proxy]
    labels:
      - traefik.enable=true
      - traefik.http.routers.hoard.rule=Host(`hoard.example.com`)
      - traefik.http.routers.hoard.entrypoints=websecure
      - traefik.http.routers.hoard.tls.certresolver=letsencrypt
      - traefik.http.services.hoard.loadbalancer.server.port=8080

networks:
  proxy:
    external: true
```

Set `TRUSTED_PROXIES` to the `proxy` network's subnet (`docker network inspect proxy -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`) and `ALLOWED_HOSTS=hoard.example.com`.

## Updating and rolling back

Updates keep your models, settings and unfinished downloads.

```bash
# published image
docker compose pull && docker compose up -d

# built from source
git pull && docker compose up -d --build
```

On `docker stop` (and so on every update), Hoard stops running downloads within a few seconds and saves them as unfinished. They continue when Hoard starts again, keeping every finished file; only a partly downloaded file starts over.

To roll back, pin the previous version (`image: ghcr.io/mr-sunglasses/hoard:1.0.0`, or `git checkout v1.0.0`) and run the same commands. [CHANGELOG.md](../CHANGELOG.md) lists what changed in each release, and anything that needs your attention.

## Backups

- **Models** are plain files in plain folders, so any backup tool works: `rsync`, restic, borg, or your NAS's snapshots. Each folder's `.hoard.json` records the repo and exact commit, so a restored model can still be updated and checked. The `.cache/` folders only hold resume data and can be left out.
- **`config/`** holds the UI-saved token and the download list. Both are easy to recreate, so backing it up is optional. If you do, keep the backup private.

To check a restored model, open it in the library and click **Check files**: every file is compared with the hashes Hugging Face published for that commit.

## Monitoring

- **Health check:** `GET /healthz` returns `{"ok": true}` with no password. Docker uses it already (`docker ps` shows `healthy`). Uptime Kuma or similar can poll it.
- **Logs:** `docker compose logs -f hoard`. Startup shows the version and any setup problem; downloads log retries and failures. Logs are rotated at 3 × 10 MB.
- **From scripts:** the [API](API.md) gives everything the UI shows. For example, to list failed downloads:

  ```bash
  curl -s -u me:$APP_PASSWORD http://nas:8080/api/downloads | jq '.[] | select(.status == "error") | {repo_id, error}'
  ```

## Running without Docker

Hoard is a normal Python app, so it also runs under systemd or on a desktop. You need [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Mr-Sunglasses/hoard.git && cd hoard
uv sync --locked --no-dev
MODELS_DIR=/srv/models CONFIG_DIR=$HOME/.config/hoard HF_HOME=/srv/models/.hf-home \
APP_PASSWORD=change-me uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --timeout-graceful-shutdown 5
```

All the settings below work as environment variables.

## Settings reference

| Setting | Default | What it does |
|---|---|---|
| `PUID` / `PGID` | `1000` | The user and group Hoard runs as (Compose only). |
| `APP_PASSWORD` | empty | Password for the UI and API; any username works. Without it, anyone who can reach Hoard can use it. |
| `HF_TOKEN` | empty | Hugging Face token for gated and private models. Wins over a token saved in the UI. |
| `ALLOWED_HOSTS` | empty | Extra host names Hoard answers to, comma separated. `*.example.com` matches subdomains; `*` turns the check off. IPs, `localhost`, single-word names and local suffixes (`.local`, `.lan`, `.home.arpa`, `.internal`…) always work. |
| `TRUSTED_PROXIES` | empty | Reverse proxies whose `X-Forwarded-For` is believed: IPs or networks, comma separated. |
| `BIND_ADDRESS` / `PORT` | `0.0.0.0` / `8080` | Where the published port listens (Compose only). |
| `MAX_PARALLEL_DOWNLOADS` | `2` | Downloads that run at once; the rest wait in a queue. |
| `MIN_FREE_GB` | `2` | Space always kept free on the models disk. Downloads that would use it are refused, and a running one stops if free space drops below half of it. |
| `MODELS_DIR` / `CONFIG_DIR` | `/models` / `/config` | Where models and settings live. Change the volumes instead when using Docker. |
