# Isolated Docker sandbox (programgarden_hanbit)

`--dangerously-skip-permissions`로 Claude Code를 돌릴 때 **호스트 맥북을 보호**하는 도커 격리
환경입니다. 에이전트는 이 프로젝트 폴더(`/workspace`)만 만질 수 있고, 홈·`~/.claude`·다른
프로젝트·시스템은 못 건드립니다. **컨테이너 안에서는 거의 모든 편집·실행이 승인 없이 자동
허용**되며, **파괴/비가역 명령만** PreToolUse hook 이 잡아 사용자 확인을 강제합니다(아래 '하드 게이트').

hanbit 은 **교육용 자동화매매** 프로젝트입니다:
- `server/` — **Python / FastAPI** 트레이딩 백엔드 (`uv` 로 의존성 관리, WebSocket 으로 시세·체결 push)
- `web/` — **Next.js** 프런트엔드 대시보드 (실시간 차트·포지션·전략 컨트롤)
- **로컬 SQLite** — `/workspace` 안 파일 1개. 클라우드 DB 없음 → 주입할 클라우드 자격증명 없음.
  (과거 시세 대량 분석이 무거워지면 DuckDB 를 시세 분석용으로 추가하는 패턴 권장.)

## 핵심 모델

```
호스트 맥                                     도커 컨테이너 (hanbit-sandbox)
────────────────────────────────────         ──────────────────────────────────
~/…/programgarden_hanbit (호스트 프로젝트 경로) ⇄ /workspace   ← 같은 파일 (bind mount)
   server/, web/, .git, *.sqlite ...           (수정/커밋이 호스트에 그대로 반영)

~/ (홈), ~/.claude, 다른 프로젝트, 시스템   ✗  컨테이너에서 접근 불가  ← 보호 경계(=목적)

GitHub (programgarden_hanbit repo)          ⇄  (선택) Claude 가 직접 — gh CLI, secret.env 의
                                                GH_TOKEN(fine-grained PAT 권장)
```

- 보호 대상은 **맥북**입니다. `/workspace` 밖(홈·`~/.claude`·다른 프로젝트·시스템)은 차단.
- 프로젝트는 호스트에 그대로 두고 bind-mount — 컨테이너 안 편집·`git commit`이 호스트에 남습니다.
- **DB 는 로컬 SQLite** 라 클라우드 토큰 주입이 없습니다. GitHub 만 **선택적**으로 엽니다.
- **하드 게이트**: skip-permissions 라도 비가역/외부 영향 명령(force-push·repo 삭제·프로젝트 밖
  `rm -rf`·`.git` 삭제 등)은 PreToolUse hook 이 사용자 확인을 강제 — 아래 '하드 게이트' 절.

## 셋업 (최초 1회, 선택)

GitHub 관리가 필요할 때만:
```bash
cp sandbox/secret.env.example sandbox/secret.env
# sandbox/secret.env 에 GH_TOKEN 입력 (gitignore 됨, 커밋 안 됨)
#  - GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
#  - programgarden_hanbit repo 하나로 스코프 권장 (Contents·Pull requests·Issues = RW)
```
> 비워두면 GitHub 없이 동작합니다(로컬 개발만).

## 빠른 시작

```bash
# 컨테이너 진입 (첫 실행은 이미지 빌드 3~5분: Python + Node + gh)
./sandbox/run-isolated.sh

# 컨테이너 안에서 — 최초 1회
(cd web && pnpm install)        # Next.js 프런트 (node_modules 볼륨이 비어 있어 Linux 바이너리로 설치)
(cd server && uv sync)          # Python 백엔드 venv (.venv 볼륨에 Linux 바이너리로 설치)
claude                          # 최초 1회 로그인 (격리 볼륨이라 호스트 인증과 분리됨)

# 이후부터 권한 스킵 모드 — 승인 없이 자동 진행 (파괴 명령만 게이트)
claude --dangerously-skip-permissions
```

또는 한 줄로: `./sandbox/run-isolated.sh claude --dangerously-skip-permissions`

> 백엔드 의존성 관리는 `uv` 입니다. `server/` 에 `pyproject.toml` 이 아직 없으면
> `uv init` 으로 시작하거나 `uv venv && uv pip install fastapi uvicorn ...` 로 설치하세요.
> Python 버전을 고정하려면 `server/.python-version` (예: `3.12`) — uv 가 알아서 받아옵니다.

## 포트

| 컨테이너 | 호스트 | 용도 |
|---|---|---|
| 3000 | http://localhost:13000 | Next.js 프런트엔드 (`web`) |
| 8000 | http://localhost:18000 | FastAPI 백엔드 (`server`) |

호스트 포트가 사용 중이면 다음 빈 포트로 자동 대체합니다(진입 로그 확인).

## node_modules / .venv 주의 (macOS ↔ Linux)

호스트 `node_modules`·`.venv`는 macOS 네이티브 바이너리라 Linux 컨테이너에서 깨집니다. 그래서
컨테이너의 `web/node_modules`·`server/.venv`는 named volume(`hanbit-web-node-modules`,
`hanbit-venv`)에 따로 둡니다 → 호스트 것은 무손상, 컨테이너 안 설치는 볼륨에만 씁니다.

> ⚠️ 프로젝트 레이아웃(`server/`·`web/`)을 바꾸면 `run-isolated.sh` 의 두 볼륨 마운트 경로도 같이 바꾸세요.

## 하드 게이트 (비가역/외부 영향 명령)

skip-permissions 는 기본적으로 *전부 자동 허용*이라, 진짜 되돌릴 수 없는 명령만 잡아 사용자 확인을
강제하는 PreToolUse hook 을 둔다:

- 스크립트: `.claude/hooks/guard-irreversible.mjs` (node, 의존성 0) — 등록: `.claude/settings.json`
- 잡는 것: force-push·`git branch -D`·`git reset --hard`·`git clean -f`·히스토리 재작성·
  `gh repo delete`·`gh repo edit --visibility`·`gh release delete`·`gh api DELETE`·
  프로덕션 배포(vercel/netlify)·`aws/gcloud delete`·**프로젝트 밖/`.git`/워크스페이스 루트 `rm -rf`**.
- 동작: `permissionDecision:"ask"` 를 돌려 **사용자 확인 프롬프트를 강제**(skip-permissions 에서도 우선).
- 안 잡는 것: 평범한 `git push`(정상 흐름), `/workspace` 안 일반 파일 삭제(git 회복 가능 + 컨테이너 격리).
  새 위험 CLI 는 스크립트의 `RULES` 에 한 줄 추가. hook 은 `claude` 기동 시 로드되므로 **변경 후 재시작 필요**.

> ⚠️ 이 게이트는 **실수(footgun) 방지 보조**지 *악성 코드/적대적 에이전트 방어 경계가 아니다.*
> **진짜 경계 = 컨테이너 격리**(`/workspace` 밖 차단). 게이트는 그 위에 얹은 한 겹 안전망일 뿐.

## Claude Code 버전 — 자가 업데이트 (리빌드 불필요)

Claude Code 는 **dev 소유 npm prefix**(`/home/dev/.npm-global`, `NPM_CONFIG_PREFIX`)에 설치되고
그 경로가 named volume `hanbit-claude-cli` 로 마운트된다. 이미지에 구운 버전은 **새 볼륨의 시드**일 뿐 —
첫 실행 때 볼륨에 복사되고, 이후 컨테이너 안의 업데이트는 볼륨에 영구 반영된다(`--rm` 이어도).

```bash
# 컨테이너 안 — 둘 다 볼륨에 persist, 호스트 리빌드 0:
claude update                                  # 빌트인 자동 업데이터
npm i -g @anthropic-ai/claude-code@latest      # 수동 (동치)
```

## Claude 메모리 공유 (`SHARE_CLAUDE_MEMORY=1`, 옵트인)

기본은 컨테이너의 `~/.claude`(로그인·설정·**대화 세션 기록**)가 격리 named
volume(`hanbit-claude-config`)에만 있다 — 호스트 `~/.claude` 와 공유 안 함.

```bash
SHARE_CLAUDE_MEMORY=1 ./sandbox/run-isolated.sh claude --dangerously-skip-permissions
```
- 공유되는 것은 **메모리뿐**: 호스트 `~/.claude/projects/-Users-…-hanbit/memory` 를 컨테이너의
  `…/projects/-workspace/memory` 에 read-write bind. 나머지(로그인·설정·세션 기록)는 격리 유지.
- 마운트는 컨테이너 *시작* 시점에 고정 → 이미 떠 있는 컨테이너에 attach 하면 적용 안 됨(나갔다 재시작).

## 팀 협업 (org) — 옛 relay 핸드오프 폐기

이 repo 는 **OrgTUI(pgtui) org 의 host 세션**으로 돈다. 핸드오프(팀원→팀장)·완료보고·recycle 은
pgtui MCP/`pgtui recycle --self` 로 한다 — 프로젝트 `CLAUDE.md` "팀 협업" 절 참조. 옛 호스트 ⇄ 독립
`.claude/relay/` 핸드오프(baton/watcher, `/handoff-to-host`·`/relay-watcher-restart`)는 **폐기**됐다.

## 관리

```bash
./sandbox/run-isolated.sh --rebuild   # 이미지 재빌드 (OS/python/gh/pnpm 변경 시. claude 버전엔 불필요)
docker volume rm hanbit-claude-config hanbit-claude-cli hanbit-web-node-modules hanbit-venv hanbit-uv-cache hanbit-pnpm-store
docker image rm hanbit-sandbox:latest
```
