"""체결 기반 실현손익 (M3b §5.3 — M3a 연기분 ①) — base = 통화단위.

일일손실 한도의 realized 권위는 **트래커/잔고 realized_pnl 이 아니라 우리 fills** 다(§13-3:
트래커 realized_pnl 이 lifetime 누적인지 일중인지 라이브 미확정). 그래서 우리가 적재한 체결
원장(fills)에서 **평균원가 매칭**으로 누적 실현손익을 직접 산출한다. 거래일 경계내 실현은
DailyLossMonitor 가 baseline(day_start_realized) 과의 차분으로 추출하므로(§5.3), 여기서는
**부호 있는 누적 실현손익**(음수=손실)만 계산한다.

평균원가 모델: 같은 방향 체결은 포지션을 키우며 가중평균 진입가를 갱신, 반대 방향 체결은
보유분을 청산해 `(체결가 − 평균진입가) × 청산수량 × 방향 × 승수` 만큼 실현을 누적한다.
방향 역전(과청산) 시 잔여분은 새 진입가로 포지션을 다시 연다.
"""

from __future__ import annotations


def realized_pnl_ccy(signed_fills: list[tuple[float, float]], multiplier: float = 1.0) -> float:
    """평균원가 매칭 누적 실현손익(통화단위, 부호 있음).

    signed_fills: 시간순 `(signed_qty, price)` — 매수는 +qty, 매도는 −qty.
    multiplier: 계약 승수(주식 1, 선물 N). 손익 = 가격차 × 수량 × 승수.
    """
    mult = float(multiplier or 1.0)
    net = 0.0      # 부호 있는 순보유(>0 롱, <0 숏)
    avg = 0.0      # 평균 진입가(보유 통화단위)
    realized = 0.0
    for raw_qty, price in signed_fills:
        q = float(raw_qty)
        p = float(price)
        if q == 0:
            continue
        same_dir = net == 0 or (net > 0) == (q > 0)
        if same_dir:
            # 증가/신규 — 가중평균 진입가 갱신(net·q 동부호라 나눗셈 안전).
            new_net = net + q
            avg = ((avg * net) + (p * q)) / new_net if new_net != 0 else 0.0
            net = new_net
            continue
        # 반대 방향 — 보유분 청산(실현 확정).
        closing = min(abs(q), abs(net))
        realized += (p - avg) * closing * (1.0 if net > 0 else -1.0) * mult
        net_after = net + q
        if net_after == 0:
            avg = 0.0
        elif (net_after > 0) != (net > 0):
            # 과청산(방향 역전) — 잔여분은 이 체결가로 새 포지션을 연다.
            avg = p
        net = net_after
    return realized
