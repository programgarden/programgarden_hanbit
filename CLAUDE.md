# programgarden_hanbit

교육용 **자동화매매 + UI** 프로젝트.

- `server/` — **Python / FastAPI** 트레이딩 백엔드. 의존성은 **uv**. 시세·체결을 **WebSocket** 으로 push.
- `web/` — **Next.js** 프런트엔드 대시보드. 실시간 차트(lightweight-charts/ECharts), 포지션·전략 컨트롤.
- **로컬 DB = SQLite** (`/workspace` 안 파일). 트랜잭션 상태(주문/체결/포지션/계좌/전략). 클라우드 DB 없음.
  - 과거 시세(OHLCV/틱) 대량 분석이 무거워지면 **DuckDB** 를 시세 분석용으로 추가(컬럼형, 파일 1개).

## 개발 환경 — 격리 Docker 샌드박스

이 프로젝트는 **격리 Docker 컨테이너** 안에서 `claude --dangerously-skip-permissions` 로 작업한다.
컨테이너가 호스트 맥북을 보호하는 보안 경계다(`/workspace` 만 접근, 홈·다른 프로젝트·시스템 차단).

```bash
./sandbox/run-isolated.sh claude --dangerously-skip-permissions
```

- 컨테이너 안에서는 **거의 모든 편집·실행이 승인 없이 자동 허용**된다.
- **파괴/비가역 명령만** PreToolUse hook(`.claude/hooks/guard-irreversible.mjs`)이 잡아 사용자 확인을 강제한다
  (force-push·repo 삭제·프로젝트 밖 `rm -rf`·`.git` 삭제 등). `/workspace` 안 일반 작업은 통과.
- 자세한 내용: `sandbox/README.md`.

## 포트

컨테이너 내부는 `3000`(web)·`8000`(server) 그대로. 호스트 노출은 충돌 회피용 전용 포트로 매핑:
- web — 컨테이너 `3000` → **호스트 `http://localhost:8000`**
- api — 컨테이너 `8000` → **호스트 `http://localhost:18000`**
  - ⚠️ 번호 의미 주의: **컨테이너 안 `8000` = api**, **호스트 `8000` = web**(호스트 8000 → 컨테이너 3000). 같은 8000 이라도 가리키는 대상이 다르다.

## 팀 협업 — 팀원 ⇄ 팀장 핸드오프 (org)

이 repo 는 **OrgTUI(pgtui) org 의 host 세션**으로 돈다 — PM → 팀장(lead) → 팀원(employee) 계층이고,
각 세션은 cmux surface 위 `claude` 가 **pgtui MCP** 에 연결된다. **예전 호스트 ⇄ 독립(docker) relay/`baton.md`
핸드오프는 폐기**했다 — 핸드오프·상태보고·완료표시는 전부 pgtui MCP 도구로 한다. `.claude/relay/`·`baton.md`
를 찾지 말 것(없다).

**자기 역할/상대 파악**: cmux surface 위면(=`$CMUX_SURFACE_ID` 존재) org host 세션이다. MCP `list_peers`
로 내가 메시지 보낼 수 있는 상대(팀장/동료/PM)를 본다.

**팀원(employee) → 팀장(lead):**
- **`mark_done`**(summary) — 맡은 서브태스크 완료. ORG 트리에 `✓ 작업완료` 로 뜨고 팀장에게 보고된다. **작업 끝내고 팀장에게 넘기는 기본 경로.**
- `send_report`(summary) — 비차단 진행 보고(팀장에게). 검토받고 싶을 때도 이걸로 맥락을 넘긴다.
- `send_message`(to, body) — 팀장/동료에게 구체 맥락 전달(`to` 예: `lead:programgarden_hanbit`). 받은 건 `read_messages` 로 확인.
- `update_status`(run/wait/idle/error) — 라이브 상태.

**팀장(lead) → PM:**
- **`request_approval`**(step, summary) — 끝낸 단계를 PM 에 보고하고 **승인까지 블록**(승인 전엔 commit/push 못 함). 거절되면 피드백 받아 리워크.
- `send_report` / `mark_done` — 비차단 보고 / 프로젝트 완료.

핸드오프 전 진행 작업은 commit 으로 체크포인트(현 브랜치, push X). 영속 맥락은 `.claude/plans/STATUS.md`
+ 연관 plan 에 남긴다 — 다음 세션/팀장이 git + 그 파일로 이어간다.

## 자동 commit 금지

작업을 임의로 commit 하지 않는다 — 사용자가 명시할 때만. **예외**: recycle 체크포인트
직전 `wip(recycle):` commit 은 의도된 예외(push 하지 않음).
