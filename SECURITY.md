# Security

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Report it privately on GitHub: [Security → Report a vulnerability](https://github.com/Mr-Sunglasses/hoard/security/advisories/new).

Include what an attacker can do, the steps to reproduce it, and the Hoard version (`docker compose logs hoard | grep "Hoard "`). Once a fix is released, the advisory is published with credit to you, unless you'd rather not be named.

## Supported versions

Fixes go into the newest release only. Images tagged with a major version (`ghcr.io/mr-sunglasses/hoard:1`) pick them up on `docker compose pull`.

## What Hoard protects against

Hoard is built for a home network, used by you and people you trust. It defends against:

- **Other websites in your browser.** A page you visit can't use Hoard through your browser: changes need a custom header that browsers only send cross-site after a CORS preflight, which Hoard never answers (CSRF). Hoard only answers to its own host names, which blocks DNS rebinding.
- **Injected content.** Data from Hugging Face is always escaped before it reaches the page, and a strict Content Security Policy blocks inline and outside scripts, framing and outside connections.
- **Password guessing.** With `APP_PASSWORD` set, ten wrong passwords lock an address out for 15 minutes. Behind a reverse proxy, set `TRUSTED_PROXIES` so this applies per visitor.
- **Path tricks.** Folder names from URLs and file paths from Hugging Face can't reach outside the models folder.
- **Token leaks.** The Hugging Face token is stored owner-only and never sent back to the browser, and it reaches download processes through a pipe, not the command line.
- **A compromised download process.** The container runs as your user, with a read-only filesystem, no Linux capabilities and no privilege escalation.

## What it doesn't

- **Plain HTTP.** Hoard has no HTTPS of its own. Use a VPN or a reverse proxy with HTTPS for anything beyond your home network ([Deployment](docs/DEPLOYMENT.md#reaching-hoard-from-other-devices)).
- **Users with the password.** Anyone who has it can do everything, including deleting models and replacing the token. There are no separate accounts or roles.
- **What's inside a model.** Hoard downloads files as they are. It marks pickle-based files (`.bin`, `.pt`, `.pth`, `.ckpt`), which can run code when loaded with `torch.load()`, and shows Hugging Face's security scan, but it doesn't scan files itself. Prefer safetensors and GGUF.

## For maintainers

CI audits the Python dependencies against known vulnerabilities on every push, and Dependabot proposes updates to dependencies, base images and CI actions each week. Release images carry signed build provenance and an SBOM.
