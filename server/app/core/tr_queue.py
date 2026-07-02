"""계좌-TR 직렬 큐 (M3b §8) — 호출건수 제한 대응.

라이브 실측(M2): 계좌 TR(CIDBQ01500/02400)을 연속 호출하면 HTTP500 "호출 거래건수를
초과". 라이브러리 `on_rate_limit="wait"` 는 **초당 레이트**만 흡수하고 이 **누적 호출건수**
한도는 못 막는다. 그래서 reconcile/aggregator/킬스위치의 계좌 TR 호출을 **버킷별 직렬 큐**로
직렬화한다(동시 in-flight=1, 최소 간격 `HANBIT_TR_MIN_INTERVAL_MS`).

설계(§8):
- **버킷별 직렬**: 버킷마다 독립 leader 가 자기 대기열을 한 번에 하나씩만 처리한다. 다른
  버킷끼리는 동시 진행(격리). leader 는 별도 백그라운드 태스크가 아니라 **대기열을 비우는
  동안의 submit 호출 자신**(baton 패턴) — 영구 태스크/명시적 close 가 필요 없다.
- **우선순위**(검증 Lens3-M2): 킬스위치 청산/취소(`KILL`) > 부트 reconcile(`BOOT`, ACTIVE
  게이트라 routine 보다 높음 — 기아로 영영 RECONCILING 방지) > routine reconcile/aggregator
  (`ROUTINE`). 숫자가 작을수록 높다.
- **aging**: routine 이 무한정 밀리지 않도록 대기시간에 비례해 유효 우선순위를 끌어올린다
  (`aging_s` 마다 1단계). 이로써 폭주하는 KILL/BOOT 사이에서도 routine 이 결국 처리된다.
- **대기(레이트) vs 호출건수 거부(HTTP500) 구분**: 레이트는 라이브러리가 흡수한다. 여기서
  잡는 예외 중 **호출건수 초과**(`is_call_count_exceeded`)만 backoff 후 **재큐**한다 —
  `order_ack_timeout`(주문 미응답)과 혼동 금지. 그 외 예외는 그대로 호출자에 전파.

⚠ **락 순서 불변식**(검증 Lens3-M2, 데드락 방지 — 코드 규칙): `submit()` 을 **`OrderLocks`
보유 중 await 하지 말 것**. 브로커 fetch(계좌 TR)는 항상 order 락 **바깥**에서 일어나야 한다
(M2 reconcile 이 이미 그렇다 — 락은 체결적용/상태전이에만 잡는다). 큐 슬롯을 기다리는 동안
order 락을 쥐고 있으면, 그 주문을 건드리려는 다른 경로와 교착할 수 있다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import count


class TrPriority(IntEnum):
    """계좌 TR 우선순위 — 숫자가 작을수록 먼저 처리(0=최상위)."""

    KILL = 0  # 킬스위치 청산/취소 — 위험감축 최상위
    BOOT = 1  # 부트 reconcile — ACTIVE 게이트, 기아 방지 위해 elevated
    ROUTINE = 2  # routine reconcile / aggregator 폴링


def is_call_count_exceeded(exc: BaseException) -> bool:
    """예외가 '호출건수 초과'(HTTP500)인가 — 재큐 대상 판정(레이트 대기와 구분).

    라이브러리 예외 타입이 확정 아니라 (1) status/status_code==500 (2) 메시지에 '거래건수'/
    '호출건수' 가 있으면 호출건수 초과로 본다. `OrderError.code`(문자열)는 500 과 안 겹친다.
    """
    for attr in ("status", "status_code"):
        if getattr(exc, attr, None) in (500, "500"):
            return True
    msg = str(exc)
    return any(marker in msg for marker in ("거래건수", "호출건수", "호출 거래"))


@dataclass
class _Job:
    seq: int
    priority: TrPriority
    enqueued: float  # clock() 기준 등록 시각 — aging 계산에 사용(재큐해도 유지)
    factory: Callable[[], Awaitable]
    future: asyncio.Future
    label: str | None = None
    attempts: int = 0  # 호출건수 초과로 재큐된 횟수


@dataclass
class _Bucket:
    waiters: list[_Job] = field(default_factory=list)
    draining: bool = False
    last_call: float | None = None  # 직전 TR 종료 시각(min-interval 게이트)


class AccountTrQueue:
    """버킷별 직렬 계좌-TR 큐(§8). `submit(bucket, priority, factory)` 단일 진입점."""

    def __init__(
        self,
        *,
        min_interval_s: float = 0.25,
        aging_s: float = 30.0,
        max_retries: int = 3,
        backoff_base_s: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        is_retryable: Callable[[BaseException], bool] = is_call_count_exceeded,
    ) -> None:
        self._min_interval = max(0.0, min_interval_s)
        self._aging_s = aging_s
        self._max_retries = max_retries
        self._backoff_base = backoff_base_s
        self._clock = clock
        self._is_retryable = is_retryable
        self._buckets: dict[str, _Bucket] = {}
        self._seq = count()
        self.calls = 0  # 성공한 TR 호출 수(메트릭/테스트)
        self.retries = 0  # 호출건수 초과 재큐 수

    @classmethod
    def from_settings(cls, settings) -> AccountTrQueue:
        ms = getattr(settings, "hanbit_tr_min_interval_ms", 250)
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            ms = 250
        return cls(min_interval_s=max(0.0, ms / 1000.0))

    async def submit(
        self,
        bucket: str,
        priority: TrPriority,
        factory: Callable[[], Awaitable],
        *,
        label: str | None = None,
    ):
        """`factory()`(계좌 TR 코루틴 생성)를 버킷 직렬 큐에 넣고 결과를 반환.

        factory 는 재큐 시 **다시 호출**되므로 매번 새 코루틴을 만들어야 한다(코루틴 인스턴스
        가 아니라 무인자 콜러블을 넘긴다). ⚠ OrderLocks 보유 중 await 금지(모듈 docstring).
        """
        loop = asyncio.get_running_loop()
        b = self._buckets.setdefault(bucket, _Bucket())
        job = _Job(
            seq=next(self._seq),
            priority=TrPriority(priority),
            enqueued=self._clock(),
            factory=factory,
            future=loop.create_future(),
            label=label,
        )
        b.waiters.append(job)
        if not b.draining:
            await self._drain(b)  # 이 호출이 leader 가 되어 대기열을 비운다
        return await job.future

    def _pick(self, waiters: list[_Job]) -> _Job:
        """유효 우선순위(기본 우선순위 - aging) 최소 + FIFO(seq) tie-break 로 다음 job 선택."""
        now = self._clock()
        best: _Job | None = None
        best_key: tuple[int, int] | None = None
        for j in waiters:
            promo = int((now - j.enqueued) / self._aging_s) if self._aging_s > 0 else 0
            eff = max(int(TrPriority.KILL), int(j.priority) - promo)
            key = (eff, j.seq)
            if best_key is None or key < best_key:
                best_key, best = key, j
        assert best is not None  # waiters 비어있지 않을 때만 호출
        return best

    async def _drain(self, b: _Bucket) -> None:
        b.draining = True
        try:
            while b.waiters:
                job = self._pick(b.waiters)
                b.waiters.remove(job)
                await self._gate(b)
                await self._run_job(b, job)
        except BaseException:
            # leader 취소/예외 — 남은 대기자가 영구 hang 하지 않도록 깨운다.
            for j in b.waiters:
                if not j.future.done():
                    j.future.cancel()
            b.waiters.clear()
            raise
        finally:
            b.draining = False

    async def _gate(self, b: _Bucket) -> None:
        """직전 호출과 최소 간격 보장(동시 in-flight=1 위에 호출간격 추가)."""
        if self._min_interval <= 0 or b.last_call is None:
            return
        elapsed = self._clock() - b.last_call
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)

    async def _run_job(self, b: _Bucket, job: _Job) -> None:
        try:
            result = await job.factory()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — 분류 후 재큐 또는 전파
            b.last_call = self._clock()
            if self._is_retryable(exc) and job.attempts < self._max_retries:
                job.attempts += 1
                self.retries += 1
                await asyncio.sleep(self._backoff_base * (2 ** (job.attempts - 1)))
                b.waiters.append(job)  # 재큐 — enqueued 유지(aging 지속)
                return
            if not job.future.done():
                job.future.set_exception(exc)
            return
        b.last_call = self._clock()
        self.calls += 1
        if not job.future.done():
            job.future.set_result(result)

    def stats(self) -> dict:
        return {"calls": self.calls, "retries": self.retries, "buckets": len(self._buckets)}
