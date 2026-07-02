# programgarden_hanbit

> **한빛미디어 교재 집필을 위한 실습용 예제 프로젝트입니다.**
> 자동화매매 시스템(트레이딩 백엔드 + 실시간 대시보드)을 **교육 목적**으로 구현한 예제 코드로,
> 책의 실습·설명에 사용하기 위해 만들어졌습니다. 학습·참고용이며, 실제 투자 판단이나
> 실거래에 그대로 사용하기 위한 것이 아닙니다.

## 구성

- **`server/`** — Python / FastAPI 트레이딩 백엔드. 의존성 관리는 [uv](https://docs.astral.sh/uv/). 시세·체결을 WebSocket 으로 push.
- **`web/`** — Next.js 프런트엔드 대시보드. 실시간 차트 · 포지션 · 주문 · 전략 · 위험 컨트롤.
- **로컬 DB** — SQLite 파일 1개(트랜잭션 상태: 주문·체결·포지션·계좌·전략). 클라우드 DB 없음.
- **`sandbox/`** — `--dangerously-skip-permissions` 로 안전하게 개발하기 위한 격리 Docker 환경(선택).

설치·실행 방법은 각 디렉터리 README 에 있습니다:
[`server/README.md`](server/README.md) · [`web/README.md`](web/README.md) · [`sandbox/README.md`](sandbox/README.md).

## 빠른 시작

```bash
# 1) 백엔드 (server/)
cd server
cp .env.example .env          # LS증권 API 키 입력 (절대 커밋 금지)
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) 프런트엔드 (web/) — 새 터미널
cd web
cp .env.local.example .env.local
pnpm install
pnpm dev                      # http://localhost:3000
```

## ⚠️ 주의

- 이 저장소는 **교육용 예제**입니다. 실거래(LIVE) 주문 경로는 안전 토글(`HANBIT_ALLOW_LIVE=false`)로 기본 차단되어 있습니다.
- API 키·비밀정보는 **절대 커밋하지 마세요**(`.env` 는 `.gitignore` 처리됨).
- 투자에는 원금 손실 위험이 있으며, 이 코드는 어떠한 수익도 보장하지 않습니다.

## 라이선스

[MIT License](LICENSE) © 2026 programgarden

이 예제 코드의 저작권은 저작자(programgarden)에게 있으며, **한빛미디어 교재 제작을 위해** 작성되었습니다.
