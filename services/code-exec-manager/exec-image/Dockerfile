# Toolbox image used for the actual exec CONTAINERS the code-exec-manager
# service (M4-02) spins up to run agent-requested code — not the manager
# service itself, which has no Dockerfile of its own yet.
#
# Exec containers run with `--network none` (README.md "Code-exec network
# access"), so every tool/library a script might need has to be baked in
# here at build time; there is no runtime `pip`/`npm install` fallback.
# Adding a package later means editing this file and rebuilding, never a
# runtime install.
FROM ubuntu:24.04

# Fixed non-root UID/GID matching the host's HOMEAI_UID/HOMEAI_GID (same
# identity agent-server itself runs as, and the same owner setup-workspace.sh
# chowns the shared workspace directory to) - so exec containers can read/
# write the bind-mounted workspace without needing it world-writable.
ARG HOMEAI_UID=1000
ARG HOMEAI_GID=1000

# --create-home populates /home/homeai now (needed for any `docker run`
# that skips M4-02's `--tmpfs /home/homeai` runtime flag, e.g. the Tier A
# tool-smoke check), but at actual runtime that directory is overlaid by a
# fresh tmpfs (hardening spec, README.md "Code-exec hardening") - Docker
# mounts plain `--tmpfs` targets 1777 (world-writable+sticky) by default, so
# `homeai` can still write into it despite the mount being owned by root.
# ENV HOME is set explicitly (not just relied on from useradd) because that
# tmpfs starts empty on every container start - nothing baked into the image
# under /home/homeai (including a passwd-derived HOME) can be assumed to
# survive to then.
#
# `userdel`/`groupdel` the base image's own pre-existing `ubuntu` account
# first: `ubuntu:24.04` bakes in a default `ubuntu` user/group at UID/GID
# 1000 (confirmed via `docker run --rm ubuntu:24.04 getent passwd 1000`),
# which collides with `groupadd`/`useradd` below whenever HOMEAI_UID/GID
# take their (also-1000) defaults - "GID '1000' already exists" building
# this exact image was how this was caught. Removing it unconditionally
# (not just when it collides) keeps this Dockerfile correct for ANY
# HOMEAI_UID/GID value, not merely the default, and there's no reason to
# keep the `ubuntu` account around in an image with its own `homeai` user.
RUN userdel -r ubuntu 2>/dev/null; groupdel ubuntu 2>/dev/null; true \
    && groupadd --gid "${HOMEAI_GID}" homeai \
    && useradd --uid "${HOMEAI_UID}" --gid "${HOMEAI_GID}" --create-home \
       --home-dir /home/homeai --shell /bin/bash homeai
ENV HOME=/home/homeai

# One layer, `--no-install-recommends` + apt list cleanup, so a single `apt`
# change doesn't leave a stale, larger intermediate layer around.
#   - python3/pip/venv, nodejs/npm: the two scripting runtimes scripts expect.
#   - git, curl, jq, ripgrep, unzip, zip: common CLI plumbing.
#   - pandoc, ffmpeg, imagemagick, poppler-utils: document/media conversion.
#   - file: content-type sniffing.
#   - bash, coreutils: already present in ubuntu:24.04's base layer, but
#     listed explicitly (not relied on implicitly) so a future, more minimal
#     base image can't silently drop them - coreutils in particular provides
#     GNU `timeout`, which the manager's per-call kill relies on.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    nodejs npm \
    git curl jq ripgrep unzip zip \
    pandoc ffmpeg imagemagick poppler-utils \
    file bash coreutils \
    && rm -rf /var/lib/apt/lists/*

# System-wide (not a venv - there's no runtime install step to activate one
# for), one layer, pinned to major version only (room for patch/minor
# upgrades on rebuild without the Dockerfile needing an edit every time).
# `--break-system-packages`: ubuntu 24.04's system python3-pip refuses a
# global install otherwise (PEP 668 "externally managed environment") - safe
# here since this image has no other Python-based system tooling relying on
# apt's own python3 package set.
RUN pip3 install --no-cache-dir --break-system-packages \
    "pandas>=2,<3" "numpy>=2,<3" "pillow>=11,<12" "openpyxl>=3,<4" \
    "matplotlib>=3,<4" "pypdf>=5,<6" "requests>=2,<3" \
    "beautifulsoup4>=4,<5" "python-dateutil>=2,<3"

USER homeai
WORKDIR /workspace
CMD ["sleep", "infinity"]

# Measured image size (`docker images homeai-exec-toolbox`): 1.88GB - well
# under the 3.5GB target (5GB acceptable per the ticket if ffmpeg/matplotlib
# pushed it further).
