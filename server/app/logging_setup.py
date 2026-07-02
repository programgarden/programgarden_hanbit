"""로깅 골격 — 레벨/포맷 설정 함수."""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


def setup_logging(level: str = "INFO") -> None:
    """루트 로거를 1회 설정한다(중복 호출 안전)."""
    global _configured
    log_level = getattr(logging, level.upper(), logging.INFO)
    if _configured:
        logging.getLogger().setLevel(log_level)
        return
    logging.basicConfig(level=log_level, format=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """이름 있는 로거를 반환한다."""
    return logging.getLogger(name)
