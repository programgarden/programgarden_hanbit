#!/usr/bin/env bash
# secret-scan.sh — 의존성 없는 경량 시크릿 스캐너 (M0)
#
# 목적: 실제 자격증명(LS appkey/secret, private key, 토큰)이 저장소에 새어 들어가지 않았는지 점검.
# 범위: git이 추적하거나 추적 대상인 파일(.gitignore 존중 → .env / node_modules / .venv 제외).
#       *.example / *.sample 은 '플레이스홀더가 비어있는지' 확인 대상으로 포함한다.
# 결과: 의심 패턴 발견 시 비-0 종료(빨강), 깨끗하면 0(초록).
#
# 한계: 정규식 기반 휴리스틱. CI에서는 gitleaks 등으로 보강 가능(이 스크립트는 기본 게이트).

set -euo pipefail
cd "$(dirname "$0")/.."

# .gitignore를 존중하는 후보 파일 목록(추적 + 미추적-비무시). git repo가 아니면 find 폴백.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t FILES < <(find . -type f \
    -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/.venv/*' \
    -not -path '*/.next/*' -not -path '*/__pycache__/*')
fi

# 점검에서 제외할 경로(이 스캐너 자신, 락파일 등 false positive 소스).
skip_path() {
  case "$1" in
    # 자신 / 락파일 / 바이너리 / 패키지 캐시(제3자 코드)
    scripts/secret-scan.sh|*.lock|*/poetry.lock|*/pnpm-lock.yaml|*/uv.lock|*.png|*.jpg|*.jpeg|*.gif|*.ico|*.pdf) return 0 ;;
    .pnpm-store/*|*/.pnpm-store/*|node_modules/*|*/node_modules/*) return 0 ;;
    # 문서·마크다운: 설명용 placeholder(appkey="YOUR_APPKEY" 등)가 많아 휴리스틱 오탐 → 제외
    *.md|.claude/plans/*|CHANGELOG*) return 0 ;;
    *) return 1 ;;
  esac
}

# 시크릿 '값'이 실제로 채워진 라인 패턴.
#  - APPKEY_X / APPSECRET_X / appsecretkey 등에 8자 이상 값이 할당됨
#  - private key 블록
#  - 흔한 토큰 prefix
VALUE_ASSIGN='(app_?key|app_?secret(key)?|secret_?key|access_?token|api_?key)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9/_+.-]{8,}'
PRIVATE_KEY='-----BEGIN ([A-Z ]+ )?PRIVATE KEY-----'
TOKEN_PREFIX='(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})'

hits=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  skip_path "$f" && continue
  # 바이너리 스킵
  if grep -Iq . "$f" 2>/dev/null; then :; else continue; fi

  # 값이 '환경변수 읽기'(process.env/os.environ/getenv 등)거나 명백한 placeholder면 안전 → 제외
  if matches=$(grep -nEi "$VALUE_ASSIGN" "$f" 2>/dev/null \
      | grep -vEi 'process\.env|import\.meta|os\.environ|getenv|process\.argv|\$\{?[A-Z_]+\}?|<[^>]+>|YOUR_|CHANGE_?ME|EXAMPLE|PLACEHOLDER|xxxx' \
      | grep -vE '[:=][[:space:]]*[a-z_][a-zA-Z_]*(\.[a-z_][a-zA-Z_]*)*[[:space:]]*[,)]?[[:space:]]*$'); then
    # 2번째 grep(대소문자 구분): 값이 따옴표 없는 '순수 알파벳 식별자'면 변수 참조로 보고 제외
    #   (예: "appsecretkey": appsecretkey / appsecretkey=appsecret). 실제 키는 숫자/기호 포함 → 계속 검출.
    echo "❌ [secret?] $f"; echo "$matches" | sed 's/^/     /'; hits=$((hits+1))
  fi
  if matches=$(grep -nE "$PRIVATE_KEY" "$f" 2>/dev/null); then
    echo "❌ [private-key] $f"; echo "$matches" | sed 's/^/     /'; hits=$((hits+1))
  fi
  if matches=$(grep -nE "$TOKEN_PREFIX" "$f" 2>/dev/null); then
    echo "❌ [token] $f"; echo "$matches" | sed 's/^/     /'; hits=$((hits+1))
  fi
done

if [ "$hits" -gt 0 ]; then
  echo ""
  echo "🔴 secret-scan: ${hits}개 의심 항목 발견 — 커밋 금지. .env(.gitignore) 로 옮기고 예제는 값을 비워두세요."
  exit 1
fi
echo "🟢 secret-scan: ${#FILES[@]}개 파일 점검, 노출된 시크릿 없음."
exit 0
