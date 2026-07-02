# server — Python / FastAPI 트레이딩 백엔드

자동화매매 엔진 + API. 의존성 관리는 **uv**, 로컬 DB 는 **SQLite**(+ 후행 DuckDB).
설계·구현 설계는 통합 계획서 [`../.claude/plans/2026-06-20-통합계획서.md`](../.claude/plans/2026-06-20-통합계획서.md)(Part I 전체설계 · Part II M2 · Part III M3),
세션 핸드오프는 [`../.claude/plans/STATUS.md`](../.claude/plans/STATUS.md) 참조.

**현재 상태: M3(포트폴리오 집계 + 위험엔진 확장) 코드 완료 — paper-only.**
해외선물 HKEX 모의 주문 파이프라인(M2) + 버킷별 집계·위험·복구·안전·API(M3).
**실거래(KR/해외주식) 주문 경로는 INV-1 로 M4 까지 닫혀 있다**(read-only 시세/계좌만).

## 실행 (격리 컨테이너 안에서)

```bash
cd server
uv sync                                              # 의존성 설치(.venv 는 named volume)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- ASGI 진입점은 **`app.main:app`** (`app/` 패키지 구조).
- 컨테이너 안 `0.0.0.0:8000` → 호스트 `http://localhost:8000`(전용 포트 매핑 시 `18000`).
- Python 3.12 (`.python-version`) — uv 가 자동으로 받아온다.

## 버킷 모델 (INV-1)

거래는 **두 버킷**으로 나뉘고, 시장↔버킷↔거래모드는 `app/core/mode_matrix.py` 가 단일권위(절대 불변):

| 버킷 | 시장 | 거래모드 | 주문 경로 |
|---|---|---|---|
| `live` | 국내주식 · 해외주식 | `live` | **닫힘(M4까지)** — 시세·계좌 read-only 만 |
| `paper` | 해외선물(HKEX 모의) | `paper` | **열림**(engine `PAPER_TRADING` 시) |

- **INV-1**: live 시장에는 `.order()` facade·주문 TR 경로가 **구조적으로 부재**(정적+행동 테스트로 증명 —
  `test_readonly_invariant.py`·`test_no_live_facade.py`). 킬스위치/집계기/flatten 어떤 경로도 LIVE 주문 0회.
- API 는 버킷 파라미터(`?bucket=live|paper`)로 스코프를 받고, 잘못된 버킷은 422.

## engine_state — 런타임 단일권위

주문 가능 여부는 **런타임 `EngineState`**(`app/core/engine_state.py`)가 권위다. config `HANBIT_ENGINE_STATE`
는 **초기 의도**일 뿐, 게이트·amend·cancel 은 모두 런타임 값을 읽는다(`ENGINE_NOT_ACTIVE` 거부).

```
READ_ONLY  ──부트 reconcile 시작──▶  RECONCILING  ──미해소 0 & 포지션동기화 OK──▶  ACTIVE
   (주문 차단)                        (주문 차단)            (paper 주문 허용; config PAPER_TRADING 전제)
```

- 부트 스테이트머신(`app/orders/boot.py`): 비터미널 주문 4상태 전수 분류 → boot reconcile → 미해소분
  `quarantined` 격리 → **unresolved>0 또는 quarantine 있으면 ACTIVE 불가**. quarantine 존재 시
  게이트는 **신규 ENTRY 차단·감축 EXIT 만 허용**.
- **예외 레인**: 위험감축 cancel(`risk_reduction=True`)·킬스위치 취소는 RECONCILING/boot 실패에도 성사
  (취소는 멱등·노출 감소 → 안전 방향).

## 엔드포인트

베이스 prefix `/api/v1`(헬스 프로브 제외). 모든 포트폴리오/계좌/위험 조회는 **집계기·reconcile 이 채운
DB 행을 읽기만 한다**(직접 계좌 TR 0 — 호출건수 보호 §11).

| 경로 | 설명 |
|---|---|
| `GET /healthz` | 라이브니스 — 항상 200 |
| **system** | |
| `GET /api/v1/system/health` | `engine_state`(런타임)·`mode`·`allow_live`·`realtime_fills`·`milestone="M3"`·시장별 세션 |
| `GET /api/v1/system/modes` | 거래모드 매트릭스(INV-1): live/live/paper |
| `GET /api/v1/system/metrics` | 메트릭(orders placed/rejected/filled·reconcile diffs·`kill_switch_engaged`·`quarantined`·`kpi_snapshots` 등) |
| `GET /api/v1/system/quarantine` | quarantined 주문 목록(§7 노출 약속) |
| `GET /api/v1/system/clock` | 서버 시각 |
| **market (M1)** | |
| `GET /api/v1/market/quote\|ohlcv` | 시세·차트(read-only) |
| **orders (M2)** | |
| `POST /api/v1/orders/quote` | 주문 견적/리스크 미리보기 (+confirm_token) |
| `POST /api/v1/orders/commit` | 주문 발사 (해외선물 paper 만; KR/OVS → 403) |
| `POST /api/v1/orders/{id}/amend\|cancel` | 정정/취소(게이트 경유) |
| `POST /api/v1/orders/reconcile` | 수동 reconcile(CIDBQ02400/01500) |
| `GET /api/v1/orders/open\|history` | 미체결 / 내역 |
| **portfolio (M3)** | |
| `GET /api/v1/portfolio` | 버킷 KPI(`bucket_kpi`)·`currency_hhi`·참고 합산(표시 전용 KRW 환산) |
| `GET /api/v1/portfolio/positions?bucket=` | 버킷-스코프 포지션(eval_krw/fx_now/fx_at_buy); bad bucket→422 |
| **accounts (M3)** | |
| `GET /api/v1/accounts` | 시장별·통화별 `balances_snapshot` |
| **risk** | |
| `GET /api/v1/risk/limits\|events` | 위험 한도 / 이벤트 |
| `GET /api/v1/risk/halt_state` | 버킷별 유효상태(active\|halted_daily\|killed) + 일일손실 진행(realized/eval·baseline·한도) |
| `POST /api/v1/risk/killswitch` | 킬스위치 — `scope`·`action`(engage/release)·`level`(1\|2)·`confirm_token` |
| **strategy (stub)** | |
| `GET /api/v1/strategy\|/allocations` | 전략·배분(M4+ 스텁) |
| **WebSocket** | |
| `WS /api/v1/stream` | event_bus push — 아래 토픽 |

### WS `/stream` 토픽

연결 시 `info`(토픽 광고·milestone) → 이후 push. `ping`→`pong`.

| 토픽 | 생산자 |
|---|---|
| `orders` | 주문 상태 전이·reconcile(found/resolved/in_doubt) |
| `fill` | 체결 적재 |
| `risk_event` | 게이트 거부·킬스위치·일일손실 등 위험 이벤트 |
| `risk.halt_state` | 킬스위치 engage/release → `states_snapshot`(`app/risk/halt.py`) |
| `portfolio_snapshot` | 집계기 tick(`persist_and_publish`) — **라이브 배선은 M4**(아래) |

## FX (환율 — base=KRW)

`app/portfolio/fx.py`. **캡 비교는 항상 보수쪽**: 명목 한도 비교는 `to_krw_ceil`(환율 올림=명목 크게=
거부 strict), orderable 헤드룸은 `to_krw_floor`. 호출부에 방향표 박제(§6).

- overseas: tracker `exchange_rate`(라이브) 우선.
- futures: tracker FX 미제공 → **고정환율 fallback** `HANBIT_FX_USD_KRW=1400`·`HANBIT_FX_HKD_KRW=180`
  (`HANBIT_FX_BUFFER_PCT=0.02`·`HANBIT_FX_TTL_S=300`). 스테일/미제공 시 `fx_estimated` 표시 + risk_event(warn).

## 킬스위치 운영 노트

`app/risk/killswitch.py`(버킷-인지 오케스트레이션). API `POST /risk/killswitch` 가 위임.

- **L1 `engage`(level 1, 기본)**: 일괄취소. **버킷 분기** — LIVE 버킷은 **명시적 no-op-with-warning**
  (`kill_switch_live_noop`), paper 만 실제 취소. **quarantine 노출 분리(§7.2)**: OrdNo 보유분은
  raw cancel-by-OrdNo(전송 후에도 상태 `quarantined` 유지 = 운영 수동 resolve)·OrdNo 없음은
  `quarantine_excluded` 보고. **`LIVE_DISABLED` 미삼킴**(paper 루프의 LIVE 진입=라우팅 버그 critical→전파).
- **L2 `engage_level2`(level 2, paper 전용)**: L1 + 포지션 flatten. **2단계 확인** — `confirm_token` 없이
  요청하면 미실행 + 토큰 발급, 재요청 시 실행. flatten 은 reduce-only EXIT(§5.5)·멱등키·장마감
  `pending_flatten`(재개 시 현재 스냅에서 재계산). **fake-test 만**(라이브 청산은 M4, §0.3/[검증3]).
- **`release`**: 해제(HALTED_DAILY 자동·KILLED 수동). engage/release 모두 audit_log + risk_event 대칭 트레일.
- quarantined 주문은 **운영자가 수동 resolve**(`/system/quarantine` 로 노출).

## ⚠ 런타임 배선 경계 (M4 연기분 — 중요)

M3 의 집계·위험 코어는 **유닛-green 컴포넌트로 완성**됐으나, 일부 **라이브 push 배선은 M4 로 연기**다
(`event_bus.py`·`api/ws.py` 주석: "account_tracker 풀 push 는 M3/M5"). 구동 중인 서버에서:

- `PortfolioAggregator`·`TrackerSource`·`DailyLossMonitor` 는 **유닛 테스트로만 동작 검증** — `main.py`
  lifespan 에 account_tracker→집계기 콜백이 아직 배선되지 않음. 따라서 **일일손실 `halt_state` 는
  자동 발동하지 않는다**(모니터 호출부가 라이브에 없음; `test_daily_loss` 로만 발동 검증, §12 DoD 매핑대로).
- 라이브에서도 갱신되는 것: 킬스위치(`killed`)·reconcile/boot 가 쓰는 risk_state·주문/체결 원장.
- 트래커 콜백 기반 KPI/포지션 보강·실시간 체결(TC2/3, flag off)·일일손실 모니터 점등은 **M4**.
- 상세·항목별 상태: `.claude/plans/2026-06-20-통합계획서.md` M3 §13.

## 개발

```bash
uv run ruff check .     # 린트
uv run pytest -q        # 단위 테스트 (fake-LS, 결정론)
```

- 설정/시크릿: `app/config.py`(pydantic-settings). 키는 `.env`(git ignore), 예시는 [`.env.example`](.env.example).
- **안전 토글**: `HANBIT_ALLOW_LIVE=false` 기본(실거래 KR/OVS M4까지 닫힘) · `HANBIT_ENGINE_STATE=READ_ONLY`
  기본(paper FUT 주문 경로 차단 — 열려면 `PAPER_TRADING`) · `HANBIT_REALTIME_FILLS=false` 기본(TC2/3 writer off).
- 거래모드 매트릭스 SoT: `app/core/mode_matrix.py`(절대 불변, INV-1).
- 계좌-TR 직렬 큐: `app/core/tr_queue.py`(버킷별 직렬·`HANBIT_TR_MIN_INTERVAL_MS=250`·KILL>BOOT>ROUTINE+aging) —
  라이브 계좌 TR 호출건수 제한 대응.
- SQLite 마이그레이션: `migrations/*.sql`(0001 초기 · 0002 M2 주문 · 0003 M3 포트폴리오/위험) →
  `app/repositories/db.py`(기동 시 자동 적용, `schema_migrations` 추적).
  ⚠ migration 실패 시(중간 ALTER 후 미기록) 복구 = `server/hanbit.db` 삭제 후 재기동(dev, 실데이터 없음).
- 실시간 시세·체결은 WebSocket 으로 프런트(web)에 push.

## 재개 / 키 주의

- 키는 `server/.env`(추적 안 됨)에 채워져 있음 — 국내+해외주식=실거래 단일 키, 해외선물=모의 계정 키.
  `HANBIT_ALLOW_LIVE=false`(실거래 주문 M4까지 차단). **절대 출력/커밋 금지.**
- 라이브 e2e(해외선물 모의 신/정/취): `uv run python scripts/live_e2e_paper_fut.py`
  (LS 계좌 **모의투자 주문권한 활성화 후** — 현재 블로커, `.claude/plans/STATUS.md` 참조).
