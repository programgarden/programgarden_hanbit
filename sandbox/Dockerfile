# syntax=docker/dockerfile:1
#
# Isolated sandbox image for running Claude Code with --dangerously-skip-permissions
# on programgarden_hanbit (educational automated-trading: Python/FastAPI backend +
# Next.js frontend + local SQLite). POLYGLOT image: Python (uv) + Node (pnpm).
#
# Filesystem/process isolation: the container can freely touch the bind-mounted project
# at /workspace, but NOT the rest of your Mac (home, ~/.claude, other projects, system).
# Network is OPEN by design. There is NO cloud DB — the project uses a LOCAL SQLite file
# inside /workspace, so there are no cloud credentials to protect. GitHub (gh) is optional
# and authed at runtime via GH_TOKEN (see run-isolated.sh). See sandbox/README.md.

FROM node:22-bookworm-slim

ARG PNPM_VERSION=10.33.0
ARG CLAUDE_CODE_VERSION=2.1.158
# NPM_CONFIG_PREFIX points npm's GLOBAL install dir at a dev-owned path (home dir, not
# /usr/local). That single change is what makes Claude Code self-updatable from INSIDE the
# container: the default node:22 global prefix is /usr/local (root-owned) and the container
# runs as non-root `dev`, so `claude update` / `npm i -g …@latest` cannot write there. With
# the prefix in $HOME — and that dir mounted as a named volume (see run-isolated.sh) — both
# the bin shim and lib live on a persisted, dev-writable volume → updates survive `exit`.
ENV DEBIAN_FRONTEND=noninteractive \
    PNPM_HOME=/home/dev/.local/share/pnpm \
    NPM_CONFIG_PREFIX=/home/dev/.npm-global \
    UV_CACHE_DIR=/home/dev/.cache/uv \
    UV_LINK_MODE=copy \
    PATH=/home/dev/.npm-global/bin:/home/dev/.local/share/pnpm:/home/dev/.local/bin:$PATH \
    CLAUDE_CONFIG_DIR=/home/dev/.claude

# System deps:
#  - git/curl/ca-certificates/openssl/gnupg — base tooling + apt keyring for gh
#  - python3 + python3-venv + python3-dev + build-essential + pkg-config — Python backend.
#    numpy/pandas/ccxt ship Linux wheels; build-essential covers source builds (e.g. ta-lib).
#  - GitHub CLI `gh` — optional repo mgmt (PR/issue/release); authed at runtime via GH_TOKEN.
# Claude Code CLI is installed LATER as the `dev` user (below) so it lands in the dev-owned,
# volume-backed npm prefix and can self-update.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates openssl gnupg \
        python3 python3-venv python3-dev build-essential pkg-config \
    && ARCH="$(dpkg --print-architecture)" \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && gh --version \
    && python3 --version \
    && corepack enable \
    && rm -rf /var/lib/apt/lists/*

# LOGIN-shell PATH: run-isolated.sh launches everything via `bash -lc`, and a login shell
# re-runs /etc/profile which RESETS PATH to a system default — dropping the Docker `ENV PATH`
# (so dev-owned npm-global/pnpm/uv bins would vanish and `claude`/`uv` would be "not found").
# /etc/profile sources /etc/profile.d/*.sh last, so prepend our bins there to win. Keep this
# in sync with the ENV PATH above.
RUN printf 'export PATH="/home/dev/.npm-global/bin:/home/dev/.local/share/pnpm:/home/dev/.local/bin:$PATH"\n' \
      > /etc/profile.d/npm-global.sh

# Non-root user `dev` at uid 1000: `claude --dangerously-skip-permissions` refuses root.
# The node:22 base ships a `node` user already holding uid 1000 — free it first.
RUN if id node >/dev/null 2>&1; then userdel -r node; fi \
    && useradd -ms /bin/bash -u 1000 dev
USER dev
WORKDIR /workspace

# pnpm (Next.js frontend) via corepack — pinned here so the first `pnpm` inside is offline.
RUN corepack prepare "pnpm@${PNPM_VERSION}" --activate

# uv (Python backend package manager — fast, reproducible) — installed as `dev` into
# ~/.local/bin (XDG, already on PATH). Latest stable; pin later via a .python-version /
# pinned uv if you need reproducibility.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && uv --version

# Pre-create mountpoints so named volumes inherit `dev` ownership on first init.
# (web/node_modules + server/.venv nest INSIDE the /workspace bind mount — see run-isolated.sh.)
RUN mkdir -p /home/dev/.claude \
             /home/dev/.local/share/pnpm \
             /home/dev/.local/bin \
             /home/dev/.npm-global \
             /home/dev/.cache/uv \
             /workspace/web/node_modules \
             /workspace/server/.venv

# Claude Code CLI — installed as `dev` into the dev-owned npm prefix ($NPM_CONFIG_PREFIX =
# /home/dev/.npm-global). This baked copy SEEDS the named volume mounted at the same path:
# Docker copies image content into an empty named volume on first run, so the volume starts
# with this pinned version, and every later `claude update` / `npm i -g …@latest` writes to
# the volume → persists across `--rm` containers with NO image rebuild. Bumping the ARG only
# changes the initial seed for brand-new volumes.
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && claude --version

CMD ["bash"]
