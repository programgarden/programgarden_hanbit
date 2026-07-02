export const meta = {
  name: 'plan-doc',
  description: '자동화매매(분산투자/위험관리/포트폴리오) 시스템 계획서 작성 — 섹션 병렬 설계 → 적대적 검토 → 종합',
  phases: [
    { title: '설계', detail: '7개 설계 섹션을 라이브러리 사실 기반으로 병렬 작성' },
    { title: '검토', detail: '각 섹션을 API 정합성·위험설계 관점에서 적대적 검토·교정' },
    { title: '종합', detail: '요약·모드 매트릭스·목차·검증목록·용어집 합성' },
  ],
}

// ── 검증된 라이브러리 사실 (GitHub 예제/README 실측) ───────────────────────
const LIB = `
[programgarden_finance 검증된 사실 — 이 범위 안에서만 단정할 것. 불확실하면 open_questions로]
- pip 라이브러리. LS증권 OpenAPI 래퍼. asyncio 비동기 중심(req_async()), 일부 req_sync().
- 진입점: from programgarden_finance import LS; ls = LS.get_instance() (싱글톤) 또는 ls = LS().
  ls.login(appkey, appsecretkey, paper_trading: bool = False) -> bool.
  * paper_trading=True → 모의투자, False → 실거래. appkey/secret은 .env에 시장별 분리 가능(APPKEY_KOREA, APPKEY, APPKEY_FUTURE 등).
  * 해외선물 모의 예제는 ls = LS()(신규 인스턴스) + paper_trading=True 사용. 다른 예제는 LS.get_instance() 싱글톤 사용.
    → 실거래 세션과 모의 세션을 동시에 운용하려면 인스턴스/세션 분리가 필요한지는 라이브러리에서 검증 필요(open_question 후보).
- OAuth 토큰 자동 발급/갱신(GenerateToken / token_manager). 레이트리밋: options=SetupOptions(on_rate_limit="wait").
- 시장 클라이언트: ls.korea_stock()(=ls.국내주식()), ls.overseas_stock(), ls.overseas_futureoption().
  각 클라이언트: .market()(시세), .chart()(과거 OHLCV), .accno()(계좌), .주문()/.order()(주문), .real()(실시간 WS).
- 시세/차트 TR: 국내 t1102(현재가)·t8451/t8452(차트) 등 다수 t코드 / 해외주식 g3101(현재가)·g3103(일봉) 등 g코드 / 해외선물 o3101(마스터)·o3103(일봉) 등 o코드.
- 주문 TR: 국내 CSPAT00601(현물주문)/00701(정정)/00801(취소); 해외주식 COSAT00311(신규)/00301(정정)/COSMT00300(취소)/00400(예약); 해외선물 CIDBT00100(신규)/00900(정정)/01000(취소).
- 계좌/잔고 TR: 국내 CSPAQ22200(예수금)/12200(잔고)/12300(잔고상세)/13700(미체결); 해외주식 COSAQ00102(예수금)/COSAQ01400(잔고)/COSOQ00201(체결)/COSOQ02701(미체결); 해외선물 CIDBQ01400(예수금)/01500(잔고)/02400(미체결).
- 실시간 WS 패턴: client = ls.<market>().real(); await client.connect(); sub = client.GSC(); sub.add_gsc_symbols([...]); sub.on_gsc_message(cb).
  코드: 국내 SC0~SC4(주문/체결 이벤트, 계좌단위 등록 공유 → SC1만 등록해도 전체 활성), S3_/K3_(체결), H1_(호가);
        해외주식 GSC(체결) GSH(호가) AS0~AS4; 해외선물 OVC(체결) OVH(호가) TC1/TC2.
- 주문→체결 추적 패턴(실측): 주문 TR로 접수 → response.rsp_cd 검증(국내 매수성공 '00040'/매도 '00039'), error_msg/status_code 확인 → block2.OrdNo 획득
  → 실시간 체결이벤트로 OrdNo 매칭(국내 SC1.body.ordno). 부분체결: unercqty(미체결수량)=='0'이면 완전체결, execqty/execprc(체결수량/가). 거부: ordxctptncode=='14'.
  → 장 마감 시간대엔 모의/실전 모두 실시간 체결 이벤트가 도달하지 않음(타임아웃 처리 필요).
- account_tracker(실측): tracker = accno.account_tracker(real_client=real, refresh_interval=60).
  콜백: on_position_change(positions: {symbol: pos(quantity, current_price, buy_price, pnl_amount, pnl_rate)}),
        on_balance_change(balance: {currency: bal(deposit, orderable_amount)}),
        on_open_orders_change(orders), on_account_pnl_change(account_pnl_rate, total_eval_amount, total_buy_amount, total_pnl_amount, position_count).
  await tracker.start()/.stop(). → 포지션/손익/예수금/계좌수익률 실시간 집계를 라이브러리가 제공.
- 모듈 구성: programgarden_finance/ls/ 아래 oauth/, korea_stock/, overseas_stock/, overseas_futureoption/, common/, models.py(SetupOptions), real_base.py, tr_base.py, token_manager.py, config.py, status.py.
`

const CONSTRAINTS = `
[프로젝트 제약 — programgarden_hanbit, 교육용]
- 백엔드 server/: Python/FastAPI, 의존성 uv, 로컬 DB=SQLite(/workspace 파일), 시세 분석 무거워지면 DuckDB 추가. 시세·체결을 WebSocket으로 프런트에 push.
- 프런트 web/: Next.js 대시보드. 실시간 차트(lightweight-charts/ECharts), 포지션·전략 컨트롤. 포트 web=3000, server=8000.
- 격리 Docker 샌드박스 안에서 개발(컨테이너 0.0.0.0 → 호스트 localhost).
[거래 모드·시장 매트릭스 — 절대 위반 금지]
- 해외주식: 실거래(소액). paper_trading=False. 실제 소액 주문, 소액 상한 강제.
- 해외선물: 모의투자. paper_trading=True. **홍콩거래소(HKEX) 종목만 가능** (예제 test_hkex_master/test_ovc_hkex 존재).
- 국내주식: 실거래(소액). paper_trading=False. 소액 상한 강제.
[핵심 요구] 분산투자 + 위험관리 + 포트폴리오 관리 가 1급 기능. 산출물은 '계획서(설계 문서)'만 — 전체 코드 구현은 범위 밖.
`

const RULES = `
[작성 규칙]
- 한국어 마크다운. 설계 수준(아키텍처/모듈/스키마/인터페이스/시퀀스/표/의사코드)까지만. 완성형 구현 코드 덤프 금지.
- 섹션 본문은 최상위 '##' 제목으로 시작하지 말 것(상위에서 번호 붙임). '###' 이하 소제목과 표/목록/다이어그램(텍스트)을 적극 사용.
- programgarden_finance API는 위 '검증된 사실' 범위에서만 단정. 그 밖은 추측하지 말고 open_questions에 '라이브러리에서 확인 필요'로 명시.
- 거래 모드·시장 매트릭스를 항상 존중. 실거래/모의 혼동을 막는 설계를 명시.
`

const CTX = LIB + '\n' + CONSTRAINTS + '\n' + RULES

const SECTIONS = [
  {
    id: 'arch', title: '시스템 아키텍처 & 모듈 구조',
    focus: `server/ 모듈 레이아웃을 설계하라. programgarden_finance를 감싸는 어댑터/브로커 계층(시장 3종 통합 인터페이스), 단일 asyncio 이벤트루프에서 LS 인스턴스와 .real() 클라이언트 공유 방식, **실거래 세션(해외주식+국내) 과 모의 세션(해외선물 HKEX) 의 분리**(paper_trading이 login 단위이므로 인스턴스/세션을 모드별로 분리해야 하는지 설계하고 검증필요 항목으로 남김), FastAPI 앱 구조(router/service/repository 계층), 백그라운드 트레이딩 엔진 태스크, 라이브러리 콜백→내부 이벤트버스→WebSocket→프런트 push 흐름, 설정/시크릿(.env 시장별 키), 동기/비동기·스레드 경계. 텍스트 컴포넌트 다이어그램과 디렉토리 트리 제안 포함.`,
  },
  {
    id: 'data', title: '데이터 모델 & 저장 계층 (SQLite / DuckDB)',
    focus: `트랜잭션 상태용 SQLite 스키마를 설계하라(테이블·핵심컬럼·PK/FK·인덱스): accounts, instruments(시장/통화/종목/거래소), orders(상태머신), fills(체결), positions, strategies, allocations(목표비중), risk_limits, risk_events, signals, audit_log. 주문 생명주기 상태(approved→submitted→accepted→partially_filled→filled/rejected/canceled/expired)와 OrdNo·체결이벤트(unercqty/execqty/ordxctptncode) 매핑. 멱등성 키와 재기동 시 reconciliation(미체결/포지션 동기화) 키 설계. 다중 통화(USD/KRW/HKD) 저장·환산. 과거 OHLCV/틱을 DuckDB(컬럼형 단일 파일)로 분리하는 기준과 테이블 스키마. SQLite 동시성(WAL) 고려.`,
  },
  {
    id: 'portfolio', title: '분산투자 & 포트폴리오 관리',
    focus: `3개 시장(해외주식/해외선물/국내주식)·종목·통화·자산군에 걸친 분산 모델을 설계하라. 목표 자산배분(allocation targets)과 비중 한도(종목당/시장당/통화당 상한), 포지션 사이징 방식 비교(고정비율/변동성기반/리스크패리티 단순화 — 교육용 수준, 트레이드오프 표), account_tracker의 실시간 PnL(pnl_amount/pnl_rate/account_pnl_rate/total_eval_amount/total_buy_amount)을 포트폴리오 집계로 활용, 리밸런싱 트리거(주기/이탈 임계)와 절차, 멀티커런시 평가(공통 기준통화 환산), 분산/집중도 지표. **모의(해외선물)와 실거래(주식)를 한 포트폴리오 뷰에서 구분 표시하되 위험은 별개 버킷으로 관리**하는 방법.`,
  },
  {
    id: 'risk', title: '위험관리 엔진',
    focus: `위험관리 엔진을 설계하라. 사전(pre-trade): orderable_amount 검증, 종목/시장/통화 노출 한도, 최대 포지션 수, 1회 주문 위험 한도, **소액 실거래 상한 강제**(해외주식·국내). 사후(post-trade): 손절/익절·트레일링스탑 자동화, **일일 손실 한도 도달 시 신규주문 전면 차단**. **킬스위치/긴급정지**와 전 시장 청산 절차. **모드 안전가드**: 해외선물=paper_trading=True+HKEX 한정 강제, 해외주식·국내=실거래지만 소액 캡 — 실거래에 모의 종목/과대 금액이 가는 사고 방지 가드. 장마감 체결이벤트 미도달 대응(주문 타임아웃→재조회 reconcile). 레이트리밋(on_rate_limit). 위험 이벤트 로깅·알림·감사. 사전체크 의사결정 표(통과/거부/경고).`,
  },
  {
    id: 'strategy', title: '전략 엔진 & 주문 실행 파이프라인',
    focus: `전략 엔진과 주문 실행 파이프라인을 설계하라. 시장 무관 전략 인터페이스(on_bar/on_tick/on_signal → 표준 시그널), 시그널→위험검증→주문→체결추적 파이프라인(국내 CSPAT00601→SC1 / 해외주식 COSAT00311→GSC체결+계좌추적 / 해외선물 CIDBT00100(paper)→OVC, OrdNo 매칭, 부분체결·거부·타임아웃 처리), 주문 상태머신·멱등성·정정/취소·재시도, 실시간 구독 관리(심볼 add/remove·재연결), 거래시간 스케줄링(국내/미국/HKEX 세션), 동일 인터페이스로 모의→실거래 승격이 가능한 구조. 예시 전략 1~2개(분산 모멘텀 + 주기 리밸런싱)를 의사코드로 시연.`,
  },
  {
    id: 'frontend', title: '프런트엔드 대시보드 & 실시간 API 계약',
    focus: `Next.js 대시보드와 백엔드 API 계약을 설계하라. 페이지/뷰: 계좌·포트폴리오 요약(수익률·평가액), 포지션·잔고, 주문/체결 내역, 실시간 차트(lightweight-charts), 전략 컨트롤(시작/정지/파라미터), 위험 한도·킬스위치 패널, 실거래/모의 모드 뱃지. REST 엔드포인트 목록(계좌/포지션/주문/전략/리스크설정)과 WebSocket 토픽·메시지 JSON 스키마(시세/체결/포지션/PnL/리스크이벤트). 로컬 인증, **실거래 주문은 UI 명시적 확인(2단계)**, 상태관리/데이터fetching 접근. 프런트↔백 데이터 계약 표.`,
  },
  {
    id: 'ops', title: '안전·운영·테스트 & 단계별 로드맵',
    focus: `안전·운영·테스트·로드맵을 설계하라. 시크릿 관리(.env 시장별 키·git 제외), **모의→실거래 승격 게이트(체크리스트)**, 로깅/감사추적, 장애·재기동 복구(시작 시 미체결/포지션 reconciliation), 테스트 전략(라이브러리 모킹 test_*_mock 참조·단위/통합/페이퍼), 교육용 안전 가드레일(소액 강제·실거래 경고·모의 우선), 관측성(메트릭). **단계별 로드맵/마일스톤**을 표로: M0 스캐폴딩 → M1 인증·시세 read-only → M2 페이퍼 주문(해외선물 HKEX 모의) → M3 포트폴리오·위험엔진 → M4 소액 실거래(국내·해외주식) → M5 프런트 통합·실시간. 각 마일스톤 산출물·완료기준(DoD)·선행조건.`,
  },
]

const DESIGN_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['section_id', 'title', 'summary', 'markdown', 'open_questions'],
  properties: {
    section_id: { type: 'string' },
    title: { type: 'string' },
    summary: { type: 'string', description: '이 섹션 2~3문장 요약(한국어)' },
    markdown: { type: 'string', description: "섹션 본문(한국어 마크다운, '##' 최상위 제목 없이 '###' 이하 사용)" },
    open_questions: { type: 'array', items: { type: 'string' }, description: '라이브러리/요구사항에서 확인 필요한 항목' },
  },
}

const CRITIQUE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'issues', 'revised_markdown', 'extra_open_questions'],
  properties: {
    verdict: { type: 'string', enum: ['ok', 'revised'] },
    issues: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['severity', 'note'],
        properties: { severity: { type: 'string', enum: ['high', 'med', 'low'] }, note: { type: 'string' } },
      },
    },
    revised_markdown: { type: 'string', description: '교정·보강한 섹션 전체 마크다운(한국어)' },
    extra_open_questions: { type: 'array', items: { type: 'string' } },
  },
}

const SYNTH_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['title', 'executive_summary_md', 'mode_matrix_md', 'toc_md', 'verification_checklist_md', 'glossary_md'],
  properties: {
    title: { type: 'string', description: '계획서 제목(한국어)' },
    executive_summary_md: { type: 'string', description: '요약: 목표·범위·핵심 설계결정·비범위(한국어 마크다운)' },
    mode_matrix_md: { type: 'string', description: '거래 모드·시장 매트릭스 표(시장/모드/paper_trading/거래소/위험상한 등)' },
    toc_md: { type: 'string', description: '본문 7개 섹션 목차(번호 1~7)' },
    verification_checklist_md: { type: 'string', description: '집계된 open_questions를 정리한 라이브러리/요구사항 확인 체크리스트' },
    glossary_md: { type: 'string', description: 'TR코드/실시간코드/도메인 용어집' },
  },
}

const designPrompt = (s) => `당신은 트레이딩 시스템 아키텍트다. 아래 검증된 사실과 제약만으로 계획서의 한 섹션을 상세히 설계하라.\n\n${CTX}\n\n[담당 섹션] ${s.title}\n[설계 지시] ${s.focus}\n\nDESIGN_SCHEMA로 반환. markdown은 표·목록·텍스트 다이어그램·의사코드를 적극 활용해 충분히 구체적으로.`

const critiquePrompt = (d) => `당신은 깐깐한 시니어 리뷰어다. 아래 설계 섹션을 적대적으로 검토하라.\n\n${CTX}\n\n[검토 대상 섹션] ${d.title}\n[원본 마크다운]\n${d.markdown}\n\n검토 기준:\n1) programgarden_finance API 사용이 '검증된 사실'과 일치하는가 — 존재하지 않는 메서드/잘못된 TR코드/모드(paper_trading) 가정 오류를 찾아라. 사실 범위를 벗어난 단정은 제거하거나 open_question으로 강등.\n2) 위험관리·분산·포트폴리오 설계의 구멍(누락된 한도/사고 시나리오/엣지케이스).\n3) 거래 모드·시장 매트릭스(해외주식 실거래소액 / 해외선물 모의 HKEX / 국내 실거래소액)와의 모순.\n4) 누락된 핵심 요소.\n문제를 issues로 나열하고, 교정·보강한 전체 마크다운을 revised_markdown으로 반환하라(문제 없으면 verdict=ok, 원본을 다듬어 그대로 반환). 한국어.`

const synthPrompt = (digest) => `당신은 계획서 편집자다. 아래는 자동화매매 시스템 계획서의 7개 섹션 요약과 확인필요 항목이다.\n\n${CTX}\n\n[섹션 요약]\n${digest}\n\n전체 문서의 프런트매터를 작성하라(SYNTH_SCHEMA):\n- title: 프로젝트(programgarden_hanbit)에 맞는 계획서 제목.\n- executive_summary_md: 목표/범위/핵심 설계결정 6~10개/비범위. \n- mode_matrix_md: 시장×모드 매트릭스 표(시장|모드|paper_trading|거래소|주문API|위험상한 성격).\n- toc_md: 1~7 섹션 목차(아래 순서: 1 시스템 아키텍처, 2 데이터 모델, 3 분산투자·포트폴리오, 4 위험관리, 5 전략엔진·주문실행, 6 프런트엔드·API계약, 7 안전·운영·로드맵).\n- verification_checklist_md: 모든 open_questions를 묶어 '라이브러리 확인 필요' / '요구사항 확정 필요'로 분류한 체크박스 목록.\n- glossary_md: TR코드(CSPAT00601 등)·실시간코드(SC1/GSC/OVC 등)·도메인 용어 표.\n한국어.`

// ── 실행 ──────────────────────────────────────────────────────────────────
phase('설계')
log(`${SECTIONS.length}개 섹션 설계 → 검토 파이프라인 시작`)

const built = await pipeline(
  SECTIONS,
  (s) => agent(designPrompt(s), { schema: DESIGN_SCHEMA, phase: '설계', label: `design:${s.id}` }),
  (d, s) => {
    if (!d) { log(`⚠ ${s.id} 설계 실패 — 스킵`); return null }
    return agent(critiquePrompt(d), { schema: CRITIQUE_SCHEMA, phase: '검토', label: `review:${s.id}` })
      .then((c) => ({
        id: s.id,
        title: d.title || s.title,
        summary: d.summary || '',
        markdown: (c && c.revised_markdown && c.revised_markdown.length > 40) ? c.revised_markdown : d.markdown,
        open_questions: (d.open_questions || []).concat((c && c.extra_open_questions) || []),
        issues: (c && c.issues) || [],
        verdict: (c && c.verdict) || 'ok',
      }))
      .catch(() => ({ id: s.id, title: d.title || s.title, summary: d.summary || '', markdown: d.markdown, open_questions: d.open_questions || [], issues: [], verdict: 'ok' }))
  },
)

const sections = built.filter(Boolean)
const highIssues = sections.reduce((n, s) => n + s.issues.filter((i) => i.severity === 'high').length, 0)
log(`설계 ${sections.length}/${SECTIONS.length} 섹션 완료. high 이슈 ${highIssues}건 교정 반영.`)

phase('종합')
const digest = sections.map((s, i) =>
  `### ${i + 1}. ${s.title}\n요약: ${s.summary}\n확인필요: ${(s.open_questions || []).map((q) => '- ' + q).join('\n') || '(없음)'}`,
).join('\n\n')

const synth = await agent(synthPrompt(digest), { schema: SYNTH_SCHEMA, phase: '종합', label: 'synthesize' })

return {
  doc: synth,
  sections: sections.map((s) => ({ id: s.id, title: s.title, markdown: s.markdown })),
  allOpenQuestions: sections.flatMap((s) => (s.open_questions || []).map((q) => ({ section: s.title, q }))),
  meta: { sectionCount: sections.length, highIssues },
}
