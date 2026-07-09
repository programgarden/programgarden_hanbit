#!/usr/bin/env bash
#
# Run Claude Code in an isolated Docker container for programgarden_hanbit.
#
# PURPOSE: the container is the security boundary for `claude --dangerously-skip-permissions`.
# It protects the HOST MACBOOK — the agent can freely touch the bind-mounted project at
# /workspace (edit/commit, persists to the host), but cannot reach your home dir, ~/.claude,
# other projects, or system files.
#
# hanbit is an educational automated-trading project: Python/FastAPI backend (server/) +
# Next.js frontend (web/) + a LOCAL SQLite DB inside /workspace. There is NO cloud DB, so
# there are no cloud credentials to inject. GitHub (gh) is OPTIONAL — put a fine-grained
# GH_TOKEN in the gitignored sandbox/secret.env to let the isolated Claude manage the repo.
#
# Usage:
#   ./sandbox/run-isolated.sh                                   # shell, then run claude yourself
#   ./sandbox/run-isolated.sh claude --dangerously-skip-permissions
#   ./sandbox/run-isolated.sh --rebuild                         # force-rebuild the image first
#   SHARE_CLAUDE_MEMORY=1 ./sandbox/run-isolated.sh claude ...  # share Claude MEMORY with the host
#
# The container NEVER shares the host ~/.claude. Its Claude config — login, settings, AND the
# conversation/session history — lives only in an isolated named volume (survives --rebuild;
# only `docker volume rm hanbit-claude-config` clears it). CLAUDE_CONFIG_DIR points here.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="hanbit-sandbox:latest"

# --- port helpers (macOS lsof): detect a busy host port / find the next free one ---
port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
find_free_port() { local p; for ((p=$1; p<=$2; p++)); do port_in_use "$p" || { echo "$p"; return 0; }; done; return 1; }

# --- build (first run, or with --rebuild) ---
FORCE_BUILD=0
if [[ "${1:-}" == "--rebuild" ]]; then FORCE_BUILD=1; shift; fi
if [[ "$FORCE_BUILD" == "1" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[sandbox] building image $IMAGE (first time: ~3-5 min — Python + Node + gh)..."
  docker build -t "$IMAGE" "$REPO/sandbox"
fi

# --- optional secrets — from gitignored sandbox/secret.env ---
if [[ -f "$REPO/sandbox/secret.env" ]]; then
  set -a; . "$REPO/sandbox/secret.env"; set +a
fi
ENVS=()

# --- GitHub (gh CLI) — fine-grained PAT from gitignored sandbox/secret.env (OPTIONAL) ---
# Passed BY NAME so the value never lands on argv/ps. gh reads GH_TOKEN automatically;
# the bootstrap below runs `gh auth setup-git` so `git push` over https also uses it.
# Prefer a fine-grained PAT scoped to JUST the programgarden_hanbit repo (least blast radius).
if [[ -n "${GH_TOKEN:-}" ]]; then
  ENVS+=( -e GH_TOKEN )
  echo "[sandbox] GitHub access: ON (gh CLI via GH_TOKEN — capability = whatever the PAT is scoped to)"
else
  echo "[sandbox] GitHub access: OFF — set GH_TOKEN in sandbox/secret.env to enable (optional)."
fi

# --- mounts ---
# Language package dirs live in named volumes (NOT the bind mount): the host's macOS-native
# node_modules / .venv stay intact, and installs inside write LINUX binaries to the volumes.
#   - web/node_modules   (Next.js frontend deps — Linux)
#   - server/.venv       (Python backend venv — Linux; uv creates it here)
# NOTE: these nest INSIDE the /workspace bind mount, so the host must already have the
# server/ and web/ dirs (they exist in the repo). If you change the project layout, update
# these two mount paths to match.
MOUNTS=(
  -v "$REPO:/workspace"                              # the project (shared with the host)
  -v hanbit-web-node-modules:/workspace/web/node_modules   # Next.js frontend node_modules (Linux)
  -v hanbit-venv:/workspace/server/.venv                   # Python backend venv (Linux)
  -v hanbit-uv-cache:/home/dev/.cache/uv             # uv download/build cache (persist across runs)
  -v hanbit-pnpm-store:/home/dev/.local/share/pnpm   # pnpm store/bin (persist across runs)
  -v hanbit-claude-cli:/home/dev/.npm-global         # Claude Code CLI (dev-owned prefix) — self-updates persist, no rebuild
)

# Claude auth/config: an ISOLATED named volume. The container neither reads nor writes the host
# ~/.claude; login, settings, and the conversation/session history all live only here. Survives
# --rebuild (rebuild re-bakes the image, not volumes); only `docker volume rm hanbit-claude-config`
# clears it. (CLAUDE_CONFIG_DIR=/home/dev/.claude points config here.)
MOUNTS+=( -v hanbit-claude-config:/home/dev/.claude )

# SHARE_CLAUDE_MEMORY=1 (alias SHARE_CLAUDE_HOME=1) — opt-in: share ONLY the project's Claude
# MEMORY with the host, read-write, while login/settings/history stay isolated in the volume above.
# We bind the host's hanbit memory dir onto the path the sandbox Claude uses (cwd /workspace ->
# ~/.claude/projects/-workspace/memory) so both sides read/write the SAME files. Memory lives on the
# HOST (bind mount), so it survives even a full Docker wipe. Effective only on a FRESH container
# (mounts are fixed at container start) — see the prep step + attach note below.
SHARE_MEM=0
if [[ "${SHARE_CLAUDE_MEMORY:-${SHARE_CLAUDE_HOME:-0}}" == "1" ]]; then
  HOST_MEM_DIR="$HOME/.claude/projects/$(printf '%s' "$REPO" | sed 's/[^a-zA-Z0-9]/-/g')/memory"
  SANDBOX_MEM_DIR="/home/dev/.claude/projects/-workspace/memory"
  mkdir -p "$HOST_MEM_DIR"                              # ensure the host side exists (first run)
  MOUNTS+=( -v "$HOST_MEM_DIR:$SANDBOX_MEM_DIR" )       # rw: host & sandbox share the same files
  SHARE_MEM=1
  echo "[sandbox] SHARE_CLAUDE_MEMORY=1 → Claude MEMORY shared read-write with the host:"
  echo "[sandbox]   host:    $HOST_MEM_DIR"
  echo "[sandbox]   sandbox: $SANDBOX_MEM_DIR"
  echo "[sandbox]   (login/settings/history stay ISOLATED in the hanbit-claude-config volume.)"
fi

# git commit identity (read-only).
[[ -f "$HOME/.gitconfig" ]] && MOUNTS+=( -v "$HOME/.gitconfig:/home/dev/.gitconfig:ro" )

# --- pgtui orchestration bus (opt-in; no effect unless set) ---
if [[ -n "${ORC_DIR:-}" ]]; then
  MOUNTS+=( -v "$ORC_DIR:/orc" )
  # ORC_DB + ORC_ADDR let the in-container `pgtui recycle --self` self-recycle via the OrgTUI
  # deliverer: with no $CMUX_SURFACE_ID in a container it resolves its surface from the store by
  # addr. ORC_ADDR is explicit if set, else derived from the --mcp-config mcp-emp_<safe>.json arg.
  ENVS+=( -e "ORC_DB=${ORC_DB:-/orc/orc.db}" )
  ENVS+=( -e "ORC_BUS=${ORC_BUS:-host.docker.internal:9777}" )
  _orc_addr="${ORC_ADDR:-}"
  if [[ -z "$_orc_addr" ]]; then for _a in "$@"; do case "$_a" in
    *mcp-emp_*.json) _b="${_a##*/}"; _b="${_b#mcp-}"; _b="${_b%-docker.json}"; _b="${_b%.json}"; _b="${_b#emp_}"; _orc_addr="emp:${_b%_*}:${_b##*_}" ;;
    *mcp-lead_*.json) _b="${_a##*/}"; _b="${_b#mcp-}"; _b="${_b%-docker.json}"; _b="${_b%.json}"; _orc_addr="lead:${_b#lead_}" ;;
  esac; done; fi
  [[ -n "$_orc_addr" ]] && ENVS+=( -e "ORC_ADDR=$_orc_addr" )
  echo "[sandbox] pgtui bus: $ORC_DIR → /orc (ORC_DB=${ORC_DB:-/orc/orc.db}${_orc_addr:+, ORC_ADDR=$_orc_addr})"
fi
if [[ -n "${ORC_PGTUI:-}" ]]; then MOUNTS+=( -v "$ORC_PGTUI:/usr/local/bin/pgtui:ro" ); echo "[sandbox] pgtui bus: $ORC_PGTUI → /usr/local/bin/pgtui"; fi
# per-team Claude account (opt-in): forward the host-resolved token so this employee
# authenticates as its team's subscription (CLAUDE_CODE_OAUTH_TOKEN overrides the volume login).
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then ENVS+=( -e "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN" ); echo "[sandbox] pgtui: claude account token forwarded"; fi

# Default command: interactive shell. Track "no user command" so the in-container
# bootstrap can show a hint — a bare shell otherwise looks like the launch froze.
NOARGS=0
if [[ "$#" -eq 0 ]]; then NOARGS=1; set -- bash; fi
ENVS+=( -e "SANDBOX_NOARGS=$NOARGS" )

# Bootstrap (idempotent): wire GitHub (if a token is present) + the status line, then hand off.
BOOTSTRAP='
# GitHub: gh reads GH_TOKEN from env automatically; wire git so `git push` over https uses it too.
if [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
  if gh auth setup-git >/dev/null 2>&1; then
    echo "[sandbox] GitHub: gh authed via GH_TOKEN + git credential wired (git push uses the PAT)"
  else
    echo "[sandbox] warn: gh auth setup-git failed — check the GH_TOKEN scope/validity."
  fi
fi
# Status line: wire ~/.claude/settings.json → repo renderer (idempotent). ~/.claude is an isolated
# named volume that shadows anything baked into the image, so we install at runtime.
node /workspace/sandbox/ensure-statusline.mjs 2>/dev/null || true
if [ "${SANDBOX_NOARGS:-0}" = "1" ]; then
  echo
  echo "────────────────────────────────────────────────────────────"
  echo "  격리 컨테이너 셸 (dev@hanbit-sandbox:/workspace)."
  echo "  Claude 는 자동 실행되지 않습니다. 아래를 직접 실행하세요:"
  echo
  echo "      claude --dangerously-skip-permissions"
  echo
  echo "  최초 1회: 프런트  → (cd web && pnpm install)"
  echo "            백엔드  → (cd server && uv sync)   # 또는 uv venv && uv pip install -r ..."
  echo "            그다음   claude   (로그인)"
  echo "  나갈 때:  exit"
  echo "────────────────────────────────────────────────────────────"
  echo
fi
exec "$@"
'

# If a hanbit-sandbox container is already running, attach a NEW shell to it with `docker exec`
# instead of starting a second container (which would collide on the published ports).
# Exiting this shell leaves the original container running; exit the ORIGINAL shell to stop+remove it.
RUNNING="$(docker ps -q --filter "ancestor=$IMAGE" --filter status=running | head -n1)"
if [[ -n "$RUNNING" ]]; then
  echo "[sandbox] already-running container ${RUNNING:0:12} detected — attaching a new shell (avoids the port conflict)."
  echo "[sandbox] (exit here just closes this shell; the container keeps running.)"
  [[ "$SHARE_MEM" == "1" ]] && echo "[sandbox] note: SHARE_CLAUDE_MEMORY takes effect only when a container STARTS. This is an attach — exit it fully and relaunch to apply."
  exec docker exec -it "${ENVS[@]}" -w /workspace "$RUNNING" bash -lc "$BOOTSTRAP" _ "$@"
fi

# Fresh container: publish the dev-server ports.
#   host 8000 → container 3000 (web/Next.js)      host 18000 → container 8000 (api/FastAPI)
# 컨테이너 내부 포트는 3000/8000 유지; 호스트 노출만 web=8000/api=18000 으로 고정(호스트 3000~3001 대역 충돌 회피).
# 주의: 컨테이너 내부 8000=api 이지만 호스트 8000=web 이라 번호 의미가 다르다(호스트 8000 → 컨테이너 3000).
# 해당 호스트 포트가 사용 중이면 다음 빈 포트로 폴백(web)하거나 매핑을 건너뛴다.
PORT_ARGS=()
if port_in_use 8000; then
  ALT="$(find_free_port 8001 8010 || true)"
  if [[ -n "$ALT" ]]; then
    echo "[sandbox] port 8000 busy → web (Next.js) mapped to http://localhost:$ALT (host) → 3000 (container)."
    PORT_ARGS=( -p "$ALT:3000" )
  else
    echo "[sandbox] ports 8000-8010 all busy → web NOT mapped (container frontend unreachable from host)."
  fi
else
  echo "[sandbox] web (Next.js) → http://localhost:8000 (host) → 3000 (container)."
  PORT_ARGS=( -p 8000:3000 )
fi
if port_in_use 18000; then
  ALT="$(find_free_port 18001 18010 || true)"
  if [[ -n "$ALT" ]]; then
    echo "[sandbox] port 18000 busy → api (FastAPI) mapped to http://localhost:$ALT (host) → 8000 (container)."
    PORT_ARGS+=( -p "$ALT:8000" )
  else
    echo "[sandbox] ports 18000-18010 all busy → api NOT mapped (container backend unreachable from host)."
  fi
else
  echo "[sandbox] api (FastAPI) → http://localhost:18000 (host) → 8000 (container)."
  PORT_ARGS+=( -p 18000:8000 )
fi

# SHARE_CLAUDE_MEMORY: the memory bind nests at projects/-workspace/memory INSIDE the
# hanbit-claude-config volume. Pre-create that parent dir as `dev` in the volume so Docker does not
# create the nested mount's parent as root:root (which would block Claude from writing there).
# Idempotent; only when sharing memory + starting a fresh container.
if [[ "$SHARE_MEM" == "1" ]]; then
  docker run --rm -v hanbit-claude-config:/home/dev/.claude "$IMAGE" \
    bash -c 'mkdir -p /home/dev/.claude/projects/-workspace' >/dev/null 2>&1 \
    || echo "[sandbox] warn: could not pre-create the sandbox project dir; the memory mount may have limited write access."
fi

echo "[sandbox] entering container; project edits + git commits persist to the host."
echo "[sandbox] first run inside: (cd web && pnpm install) + (cd server && uv sync) + 'claude' login."
exec docker run -it --rm \
  --hostname hanbit-sandbox \
  "${PORT_ARGS[@]+"${PORT_ARGS[@]}"}" \
  "${ENVS[@]}" \
  "${MOUNTS[@]}" \
  -w /workspace \
  "$IMAGE" \
  bash -lc "$BOOTSTRAP" _ "$@"
