"""집계 입력 소스 (M3a) — .claude/plans/2026-06-20-통합계획서.md M3 §4.1.

3종 입력을 단일 정규화 Snap 으로:
- ReconcileSource(권위): M2 reconcile(CIDBQ01500) — 포지션 qty/avg/margin. order_service 가 직접
  `upsert_position_authority` 로 쓰므로(필드분할 §4.2) M3a 는 별도 클래스 불필요, M3b 에서 집계
  구독 배선 시 추가.
- TrackerSource(보강, opt-in): account_tracker 콜백(§1.2) → Snap.
  `tracker.py`(정규화 + 값바인딩 콜백).
- FakeSource(테스트): 다통화 결정론 픽스처(`fake.py`).
"""

from app.portfolio.sources.fake import fake_balances, fake_multi_ccy_book
from app.portfolio.sources.tracker import (
    balance_to_snap,
    make_tracker_callbacks,
    position_item_to_snap,
)

__all__ = [
    "fake_balances",
    "fake_multi_ccy_book",
    "balance_to_snap",
    "make_tracker_callbacks",
    "position_item_to_snap",
]
