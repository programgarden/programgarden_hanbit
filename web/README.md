# web — Next.js 프런트엔드 대시보드

실시간 차트 · 포지션 · 전략 · 위험 컨트롤. 백엔드(`server`, FastAPI/WebSocket)를 소비.
설계는 통합 계획서 [`../.claude/plans/2026-06-20-통합계획서.md`](../.claude/plans/2026-06-20-통합계획서.md) §6(Part I), 화면 도면은 [`../.claude/plans/UI_WIREFRAMES.md`](../.claude/plans/UI_WIREFRAMES.md).
현재 상태: App Shell + 6개 라우트 — 백엔드(FastAPI/WebSocket) **실연결**. (교육용 예제 — 실거래 주문 등 일부 기능은 마일스톤 진행에 따라 확장.)

스택: **Next.js 16**(App Router) · React 19 · TypeScript · **Tailwind v4** · pnpm.

## 실행 (격리 컨테이너 안에서)

```bash
cd web
pnpm install                       # node_modules 는 named volume(Linux)
pnpm dev                           # next dev -H 0.0.0.0 -p 3000 (package.json 에 설정됨)
```

- 컨테이너 안 `0.0.0.0:3000` → 호스트 `http://localhost:3000`.
- API 베이스 URL: 환경변수 `NEXT_PUBLIC_API_BASE`(예시 [`.env.local.example`](.env.local.example) → `http://localhost:8000/api/v1`). 미설정 시 모드 매트릭스는 폴백 상수 사용(백엔드 없이도 기동).

## 화면 (App Shell + 6 라우트)

| 경로 | 화면 |
|---|---|
| `/` | Overview — 계좌·포트폴리오 요약(통화별·실거래/모의 분리) |
| `/positions` | 포지션·잔고 (시장 탭) |
| `/orders` | 주문/체결 내역 |
| `/charts` | 실시간 차트 (lightweight-charts 예정) |
| `/strategies` | 전략 컨트롤 |
| `/risk` | 위험 한도·킬스위치 |

- 공통 레이아웃 `src/app/layout.tsx`: 상단 글로벌 바(모드 뱃지·WS 상태·시계·킬스위치) + 좌측 네비.
- 모드 뱃지는 서버 `/system/modes`(없으면 폴백)로 렌더 — 실거래(🔴LIVE)/모의(🟡PAPER) 구분의 1차 방어선.

## 개발

```bash
pnpm lint      # eslint
pnpm build     # 프로덕션 빌드
```

- 상태관리: TanStack Query(REST) + Zustand(WS 스트림) + React Hook Form/Zod(폼).
- 백엔드 REST/WS 실연결 완료. 실거래(LIVE) 주문 경로 등 일부 쓰기 기능은 마일스톤 진행에 따라 확장.
