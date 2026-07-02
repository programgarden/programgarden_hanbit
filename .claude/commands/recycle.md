---
description: 세션 컨텍스트 자동 재시작 — 진행 작업 commit + .claude/plans/STATUS.md 핸드오프 갱신 후, `pgtui recycle --self "/catch"`(OrgTUI deliverer)가 /clear → /catch 를 보내 새 세션이 끊김 없이 이어간다(콘텍스트 30~50% 차거나 작업 단위/세션이 끝날 때).
argument-hint: [재시작 후 이어갈 다음 작업 한 줄 — 비우면 STATUS '다음' 1순위로 판단]
---

# /recycle — 세션 자동 리사이클 (commit + STATUS 핸드오프 → deliverer 가 /clear → /catch)

**언제**: 컨텍스트가 30~50% 차거나, 작업 단위/세션이 끝났고 **이어서 할 다음 작업이 있을 때**.
**목적**: 컨텍스트를 가볍게 유지하면서 정교한 작업을 **새 세션**으로 끊김 없이 이어간다.

> **핵심 메커니즘**: 모델은 **자기 `/clear` 를 직접 못 한다** — 슬래시 명령은 사용자 입력에서만 파싱되고,
> 모델이 자기 대화에 명령을 주입할 수 없다. 그래서 recycle 은 **외부 오케스트레이션**이다. 이 repo 는
> OrgTUI org 의 **host(cmux) 세션**으로 돌므로, `pgtui recycle --self` 가 `$CMUX_SURFACE_ID` 로 자기 surface 를
> 알아내 OrgTUI **deliverer** 에 요청을 남기면, deliverer 가 idle 즉시 `cmux send` 로 `/clear → /catch <다음작업>`
> 을 **키보드 입력처럼** 그 surface 에 넣어준다. (예전 docker 독립실행 + `.claude/relay/` watcher 폴백은 폐기됐다.)

> **핵심 원칙**: `/clear` 는 현재 대화 기억을 **전부 지운다**. 이어가는 데 필요한 모든 것
> (무엇을·어디까지·**정확한 다음 행동 1가지**·주의점·블로커)은 반드시 **텍스트로**
> (`.claude/plans/STATUS.md` + 커밋된 파일)에 남겨야 한다. 새 세션이 `/catch` 로 보는 유일한 맥락은
> ① 마지막 commit + ② `.claude/plans/STATUS.md` + ③ `/catch` 에 붙는 이 메시지뿐이다.

재시작 후 이어갈 다음 작업: **$ARGUMENTS** (비어 있으면 §0.5 에서 STATUS/맥락으로 직접 작성)

---

## 0.5. 다음 작업 결정 (recycle 여부 분기)

**recycle 하기 전에 "이어서 할 작업이 실제로 있는지" 먼저 판단한다.**
1. `$ARGUMENTS` 가 있으면 → 그것이 다음 작업. §1 로.
2. 비어 있으면 `.claude/plans/STATUS.md` 의 **## 다음** 1순위 + 진행 중 plan 을 확인:
   ```bash
   sed -n '/^## 다음/,$p' .claude/plans/STATUS.md
   ```
   진행 중 작업/미완 단계가 있으면 → 그게 다음 작업.
   콘텍스트-무거움 트리거면 → 다음 작업 = "지금 하던 그 작업 계속".

**판단 결과:**
- **다음 작업이 있다** → §1 로 진행.
- **다음 작업이 없다 (완료 + 후속 없음)** → **recycle 하지 않는다.**
  1. 커밋 안 된 변경이 남아 있으면만 마무리 commit(push X). 깨끗하면 skip.
  2. 사용자에게 **"이번 세션 작업은 완료됐고 이어서 진행할 작업이 없습니다"** + 무엇을 끝냈는지 1~2줄.
  3. **여기서 멈춘다** — recycle 등록 안 함.

---

## 1. 상태 점검
- *지금까지 한 것* / *진행 중인 것* / **정확한 다음 행동 한 가지**를 분명히 한다.
- 대화에만 있고 **코드/문서에 아직 없는** 결정·발견·주의점·블로커를 식별한다(→ §3 에서 텍스트로 박제).

## 2. 진행 작업 commit (체크포인트)
```bash
git rev-parse --abbrev-ref HEAD            # 기본 브랜치면 먼저 작업 브랜치로 이동
git add -A && git status --short            # 무엇이 스테이징되는지 확인
```
- **secret(`.env`)·DB(`*.db`)·캐시가 스테이징되지 않는지 반드시 확인**(`.gitignore` 가 거르지만 눈으로 확인).
```bash
git commit -m "wip(recycle): <한 줄 — 어디까지 했나>"
```
- 변경 없으면(`nothing to commit`) 건너뛴다. **push 하지 않는다.**

## 3. .claude/plans/STATUS.md 핸드오프 갱신 (가장 중요 — 텍스트 박제)
다음 세션이 **STATUS.md 만 읽고도** 이어갈 수 있게 갱신한다:
- **마지막 업데이트** 날짜 갱신(절대 날짜로).
- **완료된 것**: 이번 세션 산출물 + 커밋 해시.
- **다음**: 맨 위에 **1순위 1개 = 정확한 다음 행동**(파일 경로·실행 명령·재개 스크립트). 블로커 있으면 명시 + 사용자 액션 여부.
- 대화에서만 오간 **결정/발견/주의점**을 여기로 옮긴다 (/clear 후엔 이게 유일한 기억).
- 상세 설계/로그는 `.claude/plans/` plan·설계 문서에 쌓고 STATUS 에서 가리킨다(STATUS = 인덱스 + 다음 행동).

## 4. STATUS(및 관련 문서) 커밋
```bash
git add .claude/plans/STATUS.md .claude/plans/*.md && git commit -m "docs(recycle): STATUS 핸드오프 — 다음 = <한 줄>"
```

## 5. recycle 등록 + 확인 (deliverer 가 /clear → /catch 전송)

자기 자신을 OrgTUI 브리지로 recycle 한다 — `$CMUX_SURFACE_ID` 로 항상 올바른 surface 를 맞춘다.
```bash
pgtui recycle --self "/catch <재시작 후 이어갈 다음 작업 한 줄>"
```
- `recycle --self: requested …` 가 뜨면 정상 — OrgTUI deliverer 가 이 세션이 **idle 이 되는 즉시** `/clear → /catch`
  를 이 surface 에 주입한다. `$CMUX_SURFACE_ID` 로 자기 surface 를 맞추므로 org·standalone 어디서 돌든 정확하다.
- `not inside a cmux surface ($CMUX_SURFACE_ID unset)` 가 뜨면 = cmux surface 밖이라 자동 recycle 불가 →
  사용자에게 수동 `/clear` 후 `/catch <다음작업>` 진입을 요청한다.

## 6. 멈춤
여기서 **멈춘다.** 추가 작업·질문하지 않는다. 곧 OrgTUI deliverer 가 `/clear → /catch` 를 보내고, 이어작업은 그 **새 세션**이 한다.
