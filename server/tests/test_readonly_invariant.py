"""주문 TR 정적 불변식 (M2 진화 → M3b §12 진화).

- **국내/해외주식 주문 TR(CSPAT*/COSAT*/COSMT*)은 app/ 어디에도 없어야 한다** — 실거래 주문
  경로는 M4까지 코드상 부재(정규식 스캔으로 드리프트 방지: 손수 유지하는 목록 대신 prefix 패턴).
- **해외선물 주문 발주 TR(CIDBT*)은 FUT 주문 어댑터 파일 한 곳에만 등장** — 발주 식별자 스코프.
  (CIDBQ* 계좌 조회 TR 은 reconcile 읽기 경로라 주석/문서 참조 허용 — 발주 경로 아님.)
- 신규/정정/취소 TR 이 어댑터에 실제 배선돼 있어야 한다(부재 회귀 방지).

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
_PORTFOLIO_DIR = (_APP_DIR / "portfolio").resolve()

# 실거래(KR/OVS) 주문 TR — 어디에도 금지
_FORBIDDEN_RE = re.compile(r"\b(?:CSPAT|COSAT|COSMT)\d{3,}\b")
# 해외선물 주문 발주 TR — FUT 어댑터에만 허용
_FUT_ORDER_RE = re.compile(r"\bCIDBT\d{3,}\b")
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

_EXPECTED_IN_ADAPTER = ("CIDBT00100", "CIDBT00900", "CIDBT01000")


def _py_files():
    return _APP_DIR.rglob("*.py")


def _is_under(path: Path, parent: Path) -> bool:
    return parent == path or parent in path.parents


def test_no_live_order_tr_in_app_source():
    offenders: list[str] = []
    for py in _py_files():
        for m in _FORBIDDEN_RE.findall(py.read_text(encoding="utf-8")):
            offenders.append(f"{py.relative_to(_APP_DIR)}: {m}")
    assert not offenders, f"live (KR/OVS) order TR identifiers found in app/: {offenders}"


def test_fut_order_tr_scoped_to_adapter():
    offenders: list[str] = []
    for py in _py_files():
        if py.resolve() == _FUT_ADAPTER:
            continue
        for m in _FUT_ORDER_RE.findall(py.read_text(encoding="utf-8")):
            offenders.append(f"{py.relative_to(_APP_DIR)}: {m}")
    assert not offenders, f"CIDBT* order TR leaked outside FUT adapter: {offenders}"


def test_fut_order_tr_present_in_adapter():
    text = _FUT_ADAPTER.read_text(encoding="utf-8")
    missing = [tr for tr in _EXPECTED_IN_ADAPTER if tr not in text]
    assert not missing, f"expected FUT order TRs missing from adapter: {missing}"


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
