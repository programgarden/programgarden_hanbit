"""주문 TR 정적 불변식 (M2 → M3b §12 → **M4b/M4c 진화**).

- **주문 발주 TR 은 각 시장의 전용 주문 어댑터 파일 한 곳에만 등장한다(스코프 봉인)** — INV-1 이
  약화가 아니라 진화한 것(M4 §12). "app/ 어디에도 없음"(M3b) → "전용 어댑터에만 있음"(M4):
    · CIDBT*(해외선물)  → overseas_future_order.py
    · CSPAT*(국내주식)  → korea_stock_order.py
    · COSAT*/COSMT*(해외주식) → overseas_stock_order.py
  그 밖의 app/ 파일에 발주 TR 리터럴이 새면(주석 포함) INV-1 누수 → 실패(정규식 드리프트 가드).
  (CIDBQ*/CSPAQ*/COSAQ*/COSOQ* 계좌 조회 TR 은 reconcile 읽기 경로라 스코프 대상 아님.)
- 신규/정정/취소 TR 이 각 어댑터에 실제 배선돼 있어야 한다(부재 회귀 방지).

M3b §12 진화 (INV-1 누수 차단을 컴파일 단계에서 박제):
- **account_tracker 계열 계좌-조회 TR(CIDBQ03000/01800/o3121)은 app/portfolio/ 밖에 등장 금지** —
  포트폴리오 집계(account_tracker→메트릭)의 입력일 뿐, 주문/위험/API/어댑터-주문 경로로 새면
  INV-1 누수. 현재 미도입 → **forward guard**(향후 드리프트를 정적으로 트랩).
- **국내/해외주식(LIVE) 시세 어댑터는 주문 facade(.order())/주문 메서드 부재** — KR/OVS 는 시세
  read-only(INV-1 구조 증명; M3a/M3b 의 account_tracker import 가 LIVE 주문으로 새지 않음).
"""

from __future__ import annotations

import re
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
_FUT_ADAPTER = (_APP_DIR / "adapters" / "overseas_future_order.py").resolve()
_KR_ADAPTER = (_APP_DIR / "adapters" / "korea_stock_order.py").resolve()
_OVS_ADAPTER = (_APP_DIR / "adapters" / "overseas_stock_order.py").resolve()
_PORTFOLIO_DIR = (_APP_DIR / "portfolio").resolve()

# 시장별 주문 발주 TR → 각각 전용 어댑터 파일에만 허용(스코프 봉인). (prefix, 허용파일, 정규식)
_FUT_ORDER_RE = re.compile(r"\bCIDBT\d{3,}\b")
_KR_ORDER_RE = re.compile(r"\bCSPAT\d{3,}\b")
_OVS_ORDER_RE = re.compile(r"\b(?:COSAT|COSMT)\d{3,}\b")
# account_tracker 계열 계좌-조회/마스터 TR — app/portfolio/ 밖 금지(forward guard, §12).
# 트레일링 \b 없음: `CIDBQ03000InBlock1`(식별자+단어문자) 형태까지 잡도록 prefix 매치.
_ACCOUNT_TR_RE = re.compile(r"\b(?:CIDBQ03000|CIDBQ01800|o3121|O3121)")

# LIVE 시세 어댑터 — 주문 경로(.order() facade / place·amend·cancel_order 메서드) 절대 부재
_LIVE_STOCK_ADAPTERS = (
    (_APP_DIR / "adapters" / "korea_stock.py").resolve(),
    (_APP_DIR / "adapters" / "overseas_stock.py").resolve(),
)
_ORDER_FACADE_RE = re.compile(r"\.order\(")
_ORDER_METHOD_RE = re.compile(r"\bdef\s+(?:place|amend|cancel)_order\b")

# 각 시장 발주 TR → 전용 어댑터 파일에만 허용(스코프 봉인) + 어댑터 내 필수 존재(배선 증명).
_ORDER_TR_SCOPE = (
    ("FUT", _FUT_ORDER_RE, _FUT_ADAPTER, ("CIDBT00100", "CIDBT00900", "CIDBT01000")),
    ("KR", _KR_ORDER_RE, _KR_ADAPTER, ("CSPAT00601", "CSPAT00701", "CSPAT00801")),
    ("OVS", _OVS_ORDER_RE, _OVS_ADAPTER, ("COSAT00301", "COSAT00311")),
)


def _py_files():
    return _APP_DIR.rglob("*.py")


def _is_under(path: Path, parent: Path) -> bool:
    return parent == path or parent in path.parents


def test_order_tr_scoped_to_dedicated_adapter():
    """발주 TR(CIDBT*/CSPAT*/COSAT*/COSMT*)은 각 전용 어댑터 파일 밖에 등장하면 INV-1 누수(§12).

    "app/ 어디에도 없음"(M3b)에서 "전용 어댑터에만 있음"(M4)으로 **더 정밀하게** 조인 것 —
    느슨해진 게 아니다. 주석·문서 문자열까지 포함해 다른 파일 등장 시 실패한다.
    """
    offenders: list[str] = []
    for label, regex, allowed, _expected in _ORDER_TR_SCOPE:
        for py in _py_files():
            if py.resolve() == allowed:
                continue
            for m in regex.findall(py.read_text(encoding="utf-8")):
                offenders.append(f"[{label}] {py.relative_to(_APP_DIR)}: {m}")
    assert not offenders, f"order TR leaked outside its dedicated adapter: {offenders}"


def test_order_tr_present_in_each_adapter():
    """각 시장 신규/정정/취소 TR 이 전용 어댑터에 실제 배선돼 있어야 한다(부재 회귀 방지)."""
    missing: list[str] = []
    for label, _regex, allowed, expected in _ORDER_TR_SCOPE:
        text = allowed.read_text(encoding="utf-8")
        missing.extend(f"[{label}] {tr}" for tr in expected if tr not in text)
    assert not missing, f"expected order TRs missing from adapter: {missing}"


# ── M3b §12 진화 ─────────────────────────────────────────────────────────────
def test_account_tracker_tr_confined_to_portfolio():
    """account_tracker 계좌-조회 TR 은 portfolio 집계 입력 — 그 밖에 등장하면 INV-1 누수.

    현재 코드에 미도입(0건) → forward guard. M4 에서 LIVE account_tracker 가 붙어도 집계
    경로(app/portfolio/) 안에만 머물고 주문/위험/API 로 새지 않음을 정적으로 강제.
    """
    offenders: list[str] = []
    for py in _py_files():
        if _is_under(py.resolve(), _PORTFOLIO_DIR):
            continue
        for m in _ACCOUNT_TR_RE.findall(py.read_text(encoding="utf-8")):
            offenders.append(f"{py.relative_to(_APP_DIR)}: {m}")
    assert not offenders, (
        f"account_tracker account-query TR leaked outside app/portfolio/: {offenders}"
    )


def test_live_stock_adapters_have_no_order_path():
    """국내/해외주식(LIVE) 어댑터는 시세 read-only — 주문 facade/메서드 부재(INV-1 구조 증명)."""
    offenders: list[str] = []
    for py in _LIVE_STOCK_ADAPTERS:
        text = py.read_text(encoding="utf-8")
        if _ORDER_FACADE_RE.search(text):
            offenders.append(f"{py.name}: .order() facade call")
        if _ORDER_METHOD_RE.search(text):
            offenders.append(f"{py.name}: place/amend/cancel_order method")
    assert not offenders, f"KR/OVS live stock adapter exposes an order path: {offenders}"
