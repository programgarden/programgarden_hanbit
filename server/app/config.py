"""애플리케이션 설정 — pydantic-settings 기반.

.env 가 없어도 기본값으로 기동되어야 한다(키 없으면 빈 문자열, allow_live=False).
환경변수 prefix 없이 .env.example 의 변수명을 그대로 읽는다.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """런타임 설정. 환경변수 / .env 에서 로드되며, 없으면 안전한 기본값."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 국내주식 실거래(live) 키
    appkey_korea: str = Field(default="", alias="APPKEY_KOREA")
    appsecret_korea: str = Field(default="", alias="APPSECRET_KOREA")

    # 해외주식 실거래(live) 키
    appkey_overseas: str = Field(default="", alias="APPKEY_OVERSEAS")
    appsecret_overseas: str = Field(default="", alias="APPSECRET_OVERSEAS")

    # 해외선물 모의(paper, HKEX) 키
    appkey_future: str = Field(default="", alias="APPKEY_FUTURE")
    appsecret_future: str = Field(default="", alias="APPSECRET_FUTURE")

    # 안전 토글 (M4까지 false 유지)
    hanbit_allow_live: bool = Field(default=False, alias="HANBIT_ALLOW_LIVE")

    # 주문 엔진 상태 (M2). 기본 READ_ONLY = 주문 차단. paper FUT 주문 경로를 열려면
    # PAPER_TRADING 으로 명시 전환. LIVE 시장(KR/OVS) 주문은 M4까지 이와 무관하게 닫힘.
    hanbit_engine_state: str = Field(default="READ_ONLY", alias="HANBIT_ENGINE_STATE")

    # 환율 (M3 — base=KRW). 라이브러리 내장 FX(overseas)가 우선, 미제공(futures)/스테일 시 고정.
    # 캡은 항상 보수쪽(ceil) 환율 사용 → fx_buffer_pct 로 안전 마진.
    # (.claude/plans/2026-06-20-통합계획서.md M3 §6)
    hanbit_fx_usd_krw: float = Field(default=1400.0, alias="HANBIT_FX_USD_KRW")
    hanbit_fx_hkd_krw: float = Field(default=180.0, alias="HANBIT_FX_HKD_KRW")
    hanbit_fx_buffer_pct: float = Field(default=0.02, alias="HANBIT_FX_BUFFER_PCT")
    hanbit_fx_ttl_s: int = Field(default=300, alias="HANBIT_FX_TTL_S")

    # 계좌-TR 직렬 큐 (M3b §8). 호출건수 제한 대응 — 버킷별 직렬 호출의 최소 간격(ms).
    # 라이브 측정 후 튜닝(M3 §8). 0 이면 간격 강제 없음(직렬만).
    # 설계: .claude/plans/2026-06-20-통합계획서.md M3 §8
    hanbit_tr_min_interval_ms: int = Field(default=250, alias="HANBIT_TR_MIN_INTERVAL_MS")

    # 실시간 체결(TC2/TC3) 스캐폴드 (M3b §10). 기본 off — TC 라이브 값/OvrsFutsOrdNo 매칭
    # 미검증(§13-5)이라 reconcile(CIDBQ02400)이 체결 권위. on 으로 켜도 런타임 EngineState 가
    # READ_ONLY/RECONCILING 이면 writer 는 강제 off(거래 안 도는데 상태 변이 금지, 검증 Lens2-M3).
    hanbit_realtime_fills: bool = Field(default=False, alias="HANBIT_REALTIME_FILLS")

    # 기타
    hanbit_db_path: str = Field(default="./hanbit.db", alias="HANBIT_DB_PATH")
    hanbit_log_level: str = Field(default="INFO", alias="HANBIT_LOG_LEVEL")
    hanbit_cors_origins: str = Field(
        default="http://localhost:13000,http://localhost:3000",
        alias="HANBIT_CORS_ORIGINS",
    )

    @property
    def cors_origins(self) -> list[str]:
        """쉼표로 구분된 CORS origin 문자열을 리스트로 변환."""
        return [o.strip() for o in self.hanbit_cors_origins.split(",") if o.strip()]

    @field_validator("hanbit_log_level")
    @classmethod
    def _normalize_log_level(cls, v: str) -> str:
        return v.strip().upper() or "INFO"

    @field_validator("hanbit_engine_state")
    @classmethod
    def _normalize_engine_state(cls, v: str) -> str:
        state = v.strip().upper() or "READ_ONLY"
        if state not in {"READ_ONLY", "PAPER_TRADING"}:
            raise ValueError(
                "HANBIT_ENGINE_STATE must be READ_ONLY or PAPER_TRADING"
            )
        return state

    @property
    def engine_trading_enabled(self) -> bool:
        """paper FUT 주문 경로가 열려 있는가(engine_state == PAPER_TRADING)."""
        return self.hanbit_engine_state == "PAPER_TRADING"

    @property
    def realtime_fills_enabled(self) -> bool:
        """실시간 체결(TC2/TC3) 구독/적재 스캐폴드를 켤지(§10). 런타임 ACTIVE 가 추가 전제."""
        return bool(self.hanbit_realtime_fills)


@lru_cache
def get_settings() -> Settings:
    """싱글턴 설정 인스턴스."""
    return Settings()
