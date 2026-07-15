"""자동매매 전략 엔진 (M5).

전략(Strategy)은 '무엇을 언제 사고팔지'만 판단해 Signal 을 낸다. StrategyEngine 이 그 Signal 을
OrderIntent 로 바꿔 **기존 order_service.place()** 로 보내고, 캡·집중도·킬스위치·엔진상태·
allow_live 안전은 **전부 기존 리스크 게이트가 강제**한다. 전략은 안전을 재구현하지 않는다 —
이 격리가 자동매매의 안전 핵심이다(자동경로엔 사람 확인 confirm_token 이 없으므로).
"""
