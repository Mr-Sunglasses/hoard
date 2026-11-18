# Changelog

Notable changes to Hoard. Versions follow [semantic versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-11-18

The first release with published images.

### Added

- Docker images for amd64 and arm64 at `ghcr.io/mr-sunglasses/hoard`, with signed build provenance and an SBOM.
- `TRUSTED_PROXIES` setting: behind a reverse proxy, a visitor who gets the password wrong ten times no longer locks everyone else out.
- **Check files** compares every file with Hugging Face's hashes for the downloaded commit; **Repair** downloads damaged or missing files again.
- Unfinished downloads continue after a restart or power cut, and network errors are retried automatically (after 15 s, 1 min and 5 min).
- Download queue: `MAX_PARALLEL_DOWNLOADS` run at once, the rest wait their turn.
- Free-space reserve (`MIN_FREE_GB`): downloads that would fill the disk are refused, and a running one stops before the disk is full.
- Pickle warnings for `.bin`, `.pt` and `.pth` files, and Hugging Face's security scan results on each repo.
- Host check (`ALLOWED_HOSTS`) against DNS rebinding, a lockout after ten wrong passwords, and a strict Content Security Policy.
- Deployment guide (reverse proxies, updates, backups, monitoring), security policy and contributing guide.

### Fixed

- Downloads waiting in the queue were lost when Hoard restarted.
- Repair deleted the damaged files even when the new download was refused (not enough space, already downloading, Hugging Face unreachable).
- Adding files to a model you already have under-counted the space needed, and the progress could pass 100%.
- An old **Check files** result stayed visible after an update, and a check could run while the same model was downloading.
- Two repos whose names map to the same folder (`a/b__c` and `a__b/c`) could be mixed up; the second is now refused.
- Opening the library wrote a test file on every load, waking sleeping disks.
- On `docker stop`, a running download could be saved as failed instead of unfinished. Hoard now stops downloads cleanly and continues them on the next start.
- Download progress bars no longer flood the container log.

## [0.1.0]

First version: browse and search Hugging Face, pick files, download into plain folders, library with update checks, password protection and the hand-drawn UI.
