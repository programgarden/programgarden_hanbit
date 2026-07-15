# server — Python / FastAPI 트레이딩 백엔드

자동화매매 엔진 + API. 의존성 관리는 **uv**, 로컬 DB 는 **SQLite**(+ 후행 DuckDB).
설계·구현 설계는 통합 계획서 [`../.claude/plans/2026-06-20-통합계획서.md`](../.claude/plans/2026-06-20-통합계획서.md)(Part I 전체설계 · Part II M2 · Part III M3),
세션 핸드오프는 [`../.claude/plans/STATUS.md`](../.claude/plans/STATUS.md) 참조.

**현재 상태: M5(자동매매 전략 엔진)까지 코드 완료.**
해외선물 HKEX 모의 주문(M2) + 포트폴리오·위험엔진(M3) + **국내·해외주식 LIVE 주문경로(M4b/c)**
+ **안전 UI/API(M4d: 킬스위치 LIVE·첫주문가드·누적캡·2단계 확인)** + **자동매매 전략 엔진(M5)**
+ **사이트 실거래 무장(arming) 토글**.

⚠ **실거래는 기본 잠겨 있다(`HANBIT_ALLOW_LIVE=false` → 실주문 0).** LIVE 주문경로 코드는
완성됐지만, 실제로 켜려면 (1) env `HANBIT_ALLOW_LIVE=true`(허용) + (2) 사이트에서 무장(강한 확인)
둘 다 필요하다. 주식 실주문은 아직 **라이브 미검증([L])** — 첫 발사는 소액·단일종목(첫주문가드).

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
| `live` | 국내주식 · 해외주식 | `live` | **배선됨(M4b/c)** — allow_live 무장 시에만 발주, 아니면 read-only |
| `paper` | 해외선물(HKEX 모의) | `paper` | **열림**(engine `PAPER_TRADING` 시) |

- **INV-1 진화(M4)**: 실거래 주문 TR(CSPAT\*/COSAT\*)은 이제 **전용 어댑터 파일에만** 존재하고
  (`korea_stock_order.py`·`overseas_stock_order.py`), 그 밖의 app/ 에 새면 정적 테스트가 잡는다
  (`test_readonly_invariant.py`). LIVE 발주는 **allow_live 무장**(registry·게이트·부트 3중 방어) +
  나머지 게이트를 모두 통과해야만 일어난다 — 무장 안 하면 LIVE 주문 0회(`test_no_live_facade.py`).
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
| `GET\|POST /api/v1/system/live-arming` | **실거래 무장 상태 조회 / 무장·해제**(2-key: env 허용 + 확인 문구) |
| **market (M1)** | |
| `GET /api/v1/market/quote\|ohlcv` | 시세·차트(read-only) |
| **orders (M2 + M4)** | |
| `POST /api/v1/orders/quote` | 주문 견적/리스크 미리보기 (+confirm_token; LIVE 는 서버측 저장 토큰) |
| `POST /api/v1/orders/commit` | 주문 발사 — 해외선물 paper + KR/OVS LIVE(allow_live 무장 시). LIVE 는 유효 confirm_token 필수, 미무장이면 403 |
| `POST /api/v1/orders/{id}/amend\|cancel` | 정정/취소(게이트 경유) |
| `POST /api/v1/orders/reconcile` | 수동 reconcile(FUT 종목별 / LIVE list-all §7) |
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
| **strategy (M5 — 자동매매)** | |
| `GET /api/v1/strategy` | 전략 목록 + 엔진 마스터 토글 상태 |
| `POST /api/v1/strategy/toggle` | 엔진 on/off(런타임) — 켜야만 발주 |
| `POST /api/v1/strategy/run` | 지금 1회 평가·발주(수동 트리거) |
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

- **L1 `engage`(level 1, 기본)**: 일괄취소. **버킷 분기 + allow_live(M4d)** — LIVE 버킷은 무장 시
  실제 취소, 미무장이면 no-op-with-warning(`kill_switch_live_noop`). paper 는 항상 실제 취소.
  **quarantine 노출 분리(§7.2)**: OrdNo 보유분은 raw cancel-by-OrdNo(상태 `quarantined` 유지 = 운영
  수동 resolve)·OrdNo 없음은 `quarantine_excluded` 보고. **`LIVE_DISABLED` 미삼킴**(라우팅 버그 전파).
- **L2 `engage_level2`(level 2)**: L1 + 포지션 flatten(**버킷별 맵**, M4d). **2단계 확인** — `confirm_token`
  없이 요청하면 미실행 + 토큰 발급, 재요청 시 실행. flatten 은 reduce-only EXIT(§5.5)·멱등키·장마감
  `pending_flatten`(재개 시 현재 스냅에서 재계산). LIVE 는 무장 시에만 발사(미무장/paper-미보유는 no-op).
- **`release`**: 해제(HALTED_DAILY 자동·KILLED 수동). engage/release 모두 audit_log + risk_event 대칭 트레일.
- quarantined 주문은 **운영자가 수동 resolve**(`/system/quarantine` 로 노출).

## 자동매매 전략 엔진 (M5)

`app/strategies/`. 전략은 '무엇을 언제 사고팔지' **Signal 만** 내고, `StrategyEngine` 이 그 Signal 을
**기존 `order_service.place()`** 로 라우팅한다 → 캡·집중도·킬스위치·엔진상태·allow_live 안전은 전부
기존 게이트가 강제(엔진은 안전 재구현 안 함). 자동경로엔 사람 확인이 없으므로 게이트가 유일 방어선.

- `base.py`(Signal/Strategy) · `threshold.py`(예제: 전일대비 ≤ -3% 매수 / 평가수익 ≥ +5% 청산) ·
  `engine.py`(run_once/run_loop, 시세 소스 주입).
- 마스터 토글 `HANBIT_STRATEGIES_ENABLED=false` 기본(켜야만 발주) + `HANBIT_STRATEGY_INTERVAL_S`
  (자동 루프 주기, 0=미기동). LIVE 발주는 여전히 allow_live 무장으로 잠김.
- `main.py` lifespan 이 엔진을 배선(예제 전략 시드 + 백그라운드 루프) — API `/strategy` 로 제어.

## ⚠ 런타임 배선 경계 (§15 연기분 — 중요)

일부 **라이브 push 배선은 §15/M4f 로 연기**다. 구동 중인 서버에서:

- `PortfolioAggregator`·`TrackerSource`·`DailyLossMonitor` 는 **유닛-green 컴포넌트** — `main.py`
  lifespan 에 account_tracker→집계기 콜백이 아직 배선되지 않음. 따라서 **일일손실 `halt_state` 자동
  발동**·**`balances_snapshot`(LIVE orderable 헤드룸)** 는 라이브에서 자동 갱신 안 된다(`test_daily_loss`
  로만 발동 검증). 이 배선이 LIVE 실발사(M4f)의 선행조건.
- 라이브에서도 갱신되는 것: 킬스위치(`killed`)·reconcile/boot 가 쓰는 risk_state·주문/체결 원장·
  누적 명목(`daily_notional_used_krw`, 발주 시 증가).
- 실시간 체결(TC2/3·SC1/AS1, flag off 스캐폴드)·시세 틱 실시간 push 도 [L]/후속.
- 상세: `.claude/plans/2026-06-20-M4-계획서.md` §15 · `2026-06-20-통합계획서.md` M3 §13.

## 개발

```bash
uv run ruff check .     # 린트
uv run pytest -q        # 단위 테스트 (fake-LS, 결정론)
```

- 설정/시크릿: `app/config.py`(pydantic-settings). 키는 `.env`(git ignore), 예시는 [`.env.example`](.env.example).
- **안전 토글(전부 기본 off — 실주문/자동발주 0)**:
  - `HANBIT_ALLOW_LIVE=false` — 실거래 **허용 ceiling**. false 면 사이트에서도 무장 불가(실주문 0).
    true 로 켜도 사이트 무장(`/system/live-arming`, 확인 문구)까지 해야 실제 발주(2-key).
  - `HANBIT_STRATEGIES_ENABLED=false` — 자동매매 엔진(켜야 발주) · `HANBIT_STRATEGY_INTERVAL_S=0`(자동 루프).
  - `HANBIT_ENGINE_STATE=READ_ONLY` — paper FUT 주문 경로(열려면 `PAPER_TRADING`).
  - `HANBIT_REALTIME_FILLS=false` — TC2/3 writer off.
  - LIVE 소액 캡: `HANBIT_LIVE_PER_ORDER_CAP_KRW`/`_USD` · `HANBIT_LIVE_DAILY_NOTIONAL_CAP_KRW`(누적) ·
    `HANBIT_LIVE_FIRST_ORDER_GUARD=true`(첫주문 단일종목 제한).
- 거래모드 매트릭스 SoT: `app/core/mode_matrix.py`(절대 불변, INV-1).
- 계좌-TR 직렬 큐: `app/core/tr_queue.py`(버킷별 직렬·`HANBIT_TR_MIN_INTERVAL_MS=250`·KILL>BOOT>ROUTINE+aging) —
  라이브 계좌 TR 호출건수 제한 대응.
- SQLite 마이그레이션: `migrations/*.sql`(0001 초기 · 0002 M2 주문 · 0003 M3 포트폴리오/위험) →
  `app/repositories/db.py`(기동 시 자동 적용, `schema_migrations` 추적).
  ⚠ migration 실패 시(중간 ALTER 후 미기록) 복구 = `server/hanbit.db` 삭제 후 재기동(dev, 실데이터 없음).
- 실시간 시세·체결은 WebSocket 으로 프런트(web)에 push.

## 재개 / 키 주의

- 키는 `server/.env`(추적 안 됨)에 채워져 있음 — 국내+해외주식=실거래 단일 키, 해외선물=모의 계정 키.
  `HANBIT_ALLOW_LIVE=false`(허용 ceiling — 실주문 0). **절대 출력/커밋 금지.**
- 라이브 e2e(해외선물 모의 신/정/취): `uv run python scripts/live_e2e_paper_fut.py`
  (LS 계좌 **모의투자 주문권한 활성화 후** — 현재 블로커, `.claude/plans/STATUS.md` 참조).
