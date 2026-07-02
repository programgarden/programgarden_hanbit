export const meta = {
  name: 'ui-wireframes',
  description: '.claude/plans/2026-06-20-통합계획서.md §6(Part I) 기반 UI 와이어프레임(ASCII 도면) 화면별 병렬 생성 → 공통 셸/범례 정합화',
  phases: [
    { title: '와이어프레임', detail: '7개 화면을 §6 계약 기반 ASCII 도면으로 병렬 작성' },
    { title: '정합화', detail: '공통 App Shell·범례·정렬 통일 + 화면 흐름도' },
  ],
}

// ── 공통 컨텍스트: 디자인 그래머 + App Shell 템플릿(§6) ───────────────────
const SHELL = `
[App Shell — 모든 화면이 이 프레임을 동일하게 사용 (좌측 네비 + 상단 글로벌 바). 폭 ~100칸, 코드펜스 안 ASCII]
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ☰ HANBIT   KR 🔴LIVE   OS 🔴LIVE   FUT 🟡PAPER·HKEX      WS● 연결    09:31:05 KST  美장중 HK장중   🔴 KILL │
├──────────────┬─────────────────────────────────────────────────────────────────────────────────┤
│ ● Overview   │                                                                                   │
│   Positions  │                                                                                   │
│   Orders     │          《 화면별 MAIN CONTENT — 여기를 각 화면이 채운다 》                       │
│   Charts     │                                                                                   │
│   Strategies │                                                                                   │
│   Risk       │                                                                                   │
│              │                                                                                   │
└──────────────┴─────────────────────────────────────────────────────────────────────────────────┘
- 상단 글로벌 바(항상 고정): 햄버거 · 로고 · 시장별 모드 뱃지(KR/OS=🔴LIVE, FUT=🟡PAPER·HKEX) · WS 연결상태 · 서버시계+시장세션 · 🔴글로벌 킬스위치.
- 좌측 네비: 현재 화면을 ● 로 강조. 항목 6개(Overview/Positions/Orders/Charts/Strategies/Risk).
`

const GRAMMAR = `
[와이어프레임 작성 규칙]
- 코드펜스(\`\`\`) 안에 박스드로잉(┌─┬┐│├┼┤└┴┘) ASCII로 '전체 화면'(상단바+좌측네비+해당 화면 본문)을 그린다. 폭은 약 100칸 기준.
- 상단바/좌측네비는 위 SHELL 라인을 그대로 재사용하고, 현재 화면을 좌측네비에서 ● 로 표시.
- 위젯엔 예시 숫자/문구를 넣어 실제처럼. 버튼=[버튼], 탭=⟨탭⟩/【활성탭】, 입력란=[____], 체크박스=☐/☑, 토글=◯/◉.
- 실거래=🔴LIVE(빨강 의미), 모의=🟡PAPER(노랑 의미)를 해당 위젯에 명시. 통화는 ₩/$/HK$ 로 구분.
- CJK는 폭이 2칸이라 완벽 정렬은 불가능 — 우변 정렬에 집착하지 말고 '레이아웃 전달'을 우선. 단, 외곽 박스는 닫히게.
- 도면 아래 별도로: 핵심 구성요소/인터랙션, 이 화면을 채우는 데이터 피드(REST 경로 + WS 토픽)를 적는다(§6 계약과 일치).
`

const CTX = SHELL + '\n' + GRAMMAR

const PAGES = [
  {
    id: 'overview', title: 'Overview — 계좌·포트폴리오 요약', route: '/',
    spec: `통화별·실거래/모의 '분리' 집계가 핵심(단일 총액 합산 금지). 상단 KPI 행: 활성전략수·열린주문수·포지션수. 통화별 카드 3개(₩ 국내 실거래 / $ 해외주식 실거래 / HK$ 해외선물 모의) 각 총평가·매입·손익(금액·율). 실거래/모의 분리 손익 영역. 시장별 분배 도넛(환율·환산시각 표기, /portfolio/allocation). 일중 PnL 스파크라인(세션/통화별). 가용 예수금(통화별). 기준통화(₩) 환산 총액은 '환율·환산시각 명시' 별도 카드로만.`,
    feeds: `REST /account/summary, /account/balance, /portfolio/allocation · WS pnl, balance, account_pnl`,
  },
  {
    id: 'positions', title: 'Positions — 포지션·잔고', route: '/positions',
    spec: `시장 탭(국내주식/해외주식/해외선물), 활성 탭 상단에 모드 뱃지 고정. 테이블 컬럼: symbol·qty·buy_price·current_price·pnl_amount·pnl_rate·market_value(시장별 통화). 행 클릭→Charts로 심볼 전환. 행마다 [빠른청산](실거래는 2단계 확인 트리거). 하단 통화별 합계 행. 손익 +초록/-빨강.`,
    feeds: `REST /positions · WS positions`,
  },
  {
    id: 'orders', title: 'Orders — 주문/체결 내역', route: '/orders',
    spec: `상단: 열린 주문(미체결) 테이블 — ordno·symbol·side·qty·price·filled_qty, 행에 [정정][취소](실거래 2단계). 하단: 체결 타임라인(시간 역순) — 상태칩 accepted/partially_filled/filled/rejected/timeout. 좌상 필터(시장/상태/심볼). 우상 [+ 새 주문] → 주문 입력 폼(견적 quote). 거부엔 사유, 타임아웃엔 '휴장/미도달' 구분.`,
    feeds: `REST /orders/open, /orders/history, /orders/quote→/orders/commit · WS orders, fill`,
  },
  {
    id: 'charts', title: 'Charts — 실시간 차트', route: '/charts',
    spec: `좌측 좁은 패널: 심볼 검색/선택(시장 선택; 해외선물은 HKEX 화이트리스트 종목만 노출). 중앙: 캔들차트(lightweight-charts) — 진입/청산 마커(▲▼), 미체결 지정가 라인(┄), 손절(SL)·익절(TP) 수평선. 상단: 심볼·현재가·등락·타임프레임 토글(1m/5m/1D), 모드 뱃지. 우측 좁은 패널: 호가창(bid/ask·size)·체결 틱 스트림. 과거봉=REST, 마지막봉 갱신/틱=WS.`,
    feeds: `REST /chart(OHLCV) · WS tick, quote`,
  },
  {
    id: 'strategies', title: 'Strategies — 전략 컨트롤', route: '/strategies',
    spec: `전략 카드 그리드. 카드: 이름·도는 시장(LIVE 시장이면 빨강 테두리+🔴LIVE 뱃지, 모의면 🟡PAPER)·상태(▶running/⏸stopped)·[시작]/[정지]·할당 자본(금액+비중%)·[파라미터 편집]. 카드에 '활성 한도' 미니패널 항상 노출(소액상한·누적명목·일손실·킬스위치 상태). 하단 또는 우측: 실행 로그 스트림(strategy_event: signal/order/skip/error) + 차단 시 risk_event 연동. 상단 합계: 할당 합 vs 가용자본(초과 시 경고).`,
    feeds: `REST /strategies, :start, :stop, /params, /allocation · WS strategy_event, risk_event`,
  },
  {
    id: 'risk', title: 'Risk — 위험 한도·킬스위치', route: '/risk',
    spec: `좌측 한도 설정 폼: 종목당 최대 노출, 시장별 일일 손실 한도, 총손실 한도(통화·세션 분리), 주문 1건 최대 금액(실거래 소액상한 연동·읽기전용 표시 가능), 누적/일일 명목 한도, 일일 주문건수·회전 상한, 최대 동시 포지션 수. [저장](변경은 PUT). 우상: 킬스위치 패널 — 글로벌 토글(2단계 확인)+시장별 토글, 발동 시 열린주문 정책 라디오(◉자동취소/◯보류). 하단/우하: 위반 로그 실시간 스트림(level: info/warn/block/killswitch, rule, scope, source 전략/사람).`,
    feeds: `REST /risk/limits, /risk/events, /risk/killswitch · WS risk_event`,
  },
  {
    id: 'order_confirm', title: '주문 확인 모달 — 실거래 2단계 확정', route: '(modal overlay)',
    spec: `화면 위 모달 오버레이(딤 배경). 헤더는 모드색(LIVE=🔴빨강 헤더 "실거래 주문 확인", PAPER=🟡노랑). 견적(quote) 표시: market·symbol·side·qty·price + est{notional 명목금액, fee 예상수수료, post_trade_exposure 체결후 노출, currency}. 만료 카운트다운(expires_in 30s). LIVE: 금액 재확인 체크박스 ☐ + "실거래 주문 확정" 명시 확인 강제. 버튼 [취소] [확정(commit)]. 리스크 사전체크 통과 요약(✓모드 ✓소액상한 ✓누적명목 ✓일손실 ✓킬스위치). PAPER는 단일단계.`,
    feeds: `REST /orders/quote → /orders/commit(confirm_token) · 이후 WS fill`,
  },
]

const PAGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['id', 'title', 'route', 'wireframe', 'components', 'data_feeds'],
  properties: {
    id: { type: 'string' },
    title: { type: 'string' },
    route: { type: 'string' },
    wireframe: { type: 'string', description: '코드펜스 없이 ASCII 도면 본문만(전체 화면: 상단바+좌측네비+본문). 약 100칸 폭.' },
    components: { type: 'array', items: { type: 'string' }, description: '핵심 구성요소/인터랙션' },
    data_feeds: { type: 'array', items: { type: 'string' }, description: 'REST 경로/WS 토픽(§6 일치)' },
  },
}

const FINAL_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['legend_md', 'shell_note_md', 'flow_md', 'pages'],
  properties: {
    legend_md: { type: 'string', description: '도면 기호 범례(🔴LIVE/🟡PAPER/●WS/버튼·탭·입력 표기 등)' },
    shell_note_md: { type: 'string', description: '공통 App Shell(상단바+좌측네비) 설명 + 셸 ASCII 1개' },
    flow_md: { type: 'string', description: '화면 전환/주문 2단계 흐름 텍스트 다이어그램' },
    pages: {
      type: 'array',
      description: '정렬·셸 일관성 교정한 화면별 최종 도면(7개 모두)',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'wireframe'],
        properties: { id: { type: 'string' }, wireframe: { type: 'string', description: '교정된 ASCII 도면 본문(코드펜스 없이)' } },
      },
    },
  },
}

const pagePrompt = (p) => `당신은 트레이딩 대시보드 UI 디자이너다. 아래 공통 셸/그래머와 화면 명세로 'ASCII 와이어프레임'을 그려라.\n\n${CTX}\n\n[화면] ${p.title}  (route: ${p.route})\n[명세] ${p.spec}\n[데이터 피드(§6)] ${p.feeds}\n\nPAGE_SCHEMA로 반환. wireframe은 상단 글로벌 바 + 좌측 네비(현재 화면 ● 강조) + 본문을 모두 포함한 전체 화면 ASCII(코드펜스 없이 텍스트만). 위젯에 예시 숫자/문구, LIVE/PAPER·통화기호 명시. 모달 화면이면 딤 배경 위 모달 박스를 그린다.`

const finalPrompt = (allJson) => `당신은 UI 도면 편집자다. 아래는 7개 화면의 ASCII 와이어프레임(JSON)이다.\n\n${CTX}\n\n[화면 도면들]\n${allJson}\n\n할 일:\n1) 7개 도면의 공통 App Shell(상단바·좌측네비)·폭·기호 사용을 '일관'되게 정렬·교정하라(외곽 박스 닫힘, 좌측네비 항목 동일, 현재화면 ● 위치만 다르게). 내용은 보존하되 어긋난 정렬/누락된 셸만 손본다.\n2) legend_md(기호 범례), shell_note_md(공통 셸 설명 + 셸 ASCII 1개), flow_md(화면 전환 + 실거래 quote→commit 2단계 흐름 텍스트 다이어그램)를 작성.\n3) pages 배열에 7개 화면 id별 교정된 wireframe(코드펜스 없이)을 모두 담아 반환.\nFINAL_SCHEMA로 반환. 한국어.`

// ── 실행 ─────────────────────────────────────────────────────────────────
phase('와이어프레임')
log(`${PAGES.length}개 화면 ASCII 와이어프레임 병렬 생성`)
const drawn = (await parallel(PAGES.map((p) => () =>
  agent(pagePrompt(p), { schema: PAGE_SCHEMA, phase: '와이어프레임', label: `draw:${p.id}` }),
))).filter(Boolean)

phase('정합화')
const allJson = JSON.stringify(drawn.map((d) => ({ id: d.id, title: d.title, route: d.route, wireframe: d.wireframe })))
const final = await agent(finalPrompt(allJson), { schema: FINAL_SCHEMA, phase: '정합화', label: 'unify' })

// 교정본 머지: final.pages 의 wireframe 우선, 없으면 원본
const fixed = {}
for (const fp of (final.pages || [])) fixed[fp.id] = fp.wireframe
const pages = PAGES.map((p) => {
  const d = drawn.find((x) => x.id === p.id) || {}
  return {
    id: p.id, title: p.title, route: p.route,
    wireframe: (fixed[p.id] && fixed[p.id].length > 40) ? fixed[p.id] : (d.wireframe || ''),
    components: d.components || [],
    data_feeds: d.data_feeds || [],
  }
})

log(`완료: ${pages.length}개 화면 도면 + 셸/범례/흐름도`)
return { legend_md: final.legend_md, shell_note_md: final.shell_note_md, flow_md: final.flow_md, pages }
