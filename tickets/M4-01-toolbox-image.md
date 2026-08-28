# M4-01 — Exec toolbox image (pre-baked, non-root)

**Milestone**: M4 · **Size**: M · **Depends on**: M0-01 · **Blocks**: M4-02

## Context

Exec containers have `--network none`, so everything a script might need must be baked into the
image (PLAN.md "Code-exec network access"). Adding packages later = edit this Dockerfile +
rebuild, never runtime installs.

## Spec

1. **`services/code-exec-manager/exec-image/Dockerfile`**:
   - `FROM ubuntu:24.04`.
   - Build args `HOMEAI_UID=1000`, `HOMEAI_GID=1000`; create group+user `homeai` with them,
     home `/home/homeai` (dir created; at runtime it's a tmpfs, so also set
     `ENV HOME=/home/homeai`).
   - apt (one layer, `--no-install-recommends`, clean lists): `python3` `python3-pip`
     `python3-venv` `nodejs` `npm` `git` `curl` `jq` `ripgrep` `unzip` `zip` `pandoc` `ffmpeg`
     `imagemagick` `poppler-utils` `file` `bash` `coreutils` (explicit for GNU `timeout`).
   - Python libs baked system-wide via pip (`--break-system-packages`, pinned-major, one
     layer): `pandas` `numpy` `pillow` `openpyxl` `matplotlib` `pypdf` `requests`
     `beautifulsoup4` `python-dateutil`.
   - `USER homeai`, `WORKDIR /workspace`, `CMD ["sleep", "infinity"]`.
2. **Build wiring**: script `services/code-exec-manager/build-exec-image.sh` →
   `docker build --build-arg HOMEAI_UID=... --build-arg HOMEAI_GID=... -t homeai-exec-toolbox:latest exec-image/`
   reading UID/GID from `.env`. Referenced in README quickstart (it's a host image, not a
   compose service — compose can't build images it never runs).
3. Keep image < 3.5 GB (check with `docker images`); if ffmpeg/matplotlib push past it, that's
   acceptable up to 5 GB — record the size in a Dockerfile comment.

## Out of scope

The manager service (M4-02); runtime package installation of any kind; GPU access in exec
containers.

## Acceptance criteria (Tier A)

- [ ] `./build-exec-image.sh` builds; `docker run --rm homeai-exec-toolbox:latest id -u` prints
      `${HOMEAI_UID}`.
- [ ] Tool smoke inside the image (single `docker run --rm` with a heredoc script):
      `python3 -c "import pandas, numpy, PIL, matplotlib, openpyxl, pypdf"`, `node -e "1"`,
      `git --version`, `ffmpeg -version`, `pandoc -v`, `rg --version`, `timeout 1 sleep 0` —
      all exit 0.
- [ ] Hardening dry-run — the image works under the M4-02 runtime flags:
      `docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /home/homeai --cap-drop ALL --user $UID:$GID -v /srv/homeai/workspace:/workspace homeai-exec-toolbox:latest bash -c 'echo ok > /workspace/.toolbox-check && cat /workspace/.toolbox-check && rm /workspace/.toolbox-check'`
      prints `ok`.

## Tier B

None.
