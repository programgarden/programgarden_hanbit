#!/bin/sh
# 격리 샌드박스용 Claude Code 상태 줄 렌더러.
# 출력: "ctx:NN% | <branch> | <folder> | <model>"
#
# 이 컨테이너에는 jq 가 없으므로(node22 베이스 이미지) node 로 JSON 을 파싱한다.
# 단일 출처는 repo 의 이 파일이며, run-isolated.sh 의 부트스트랩이
# ~/.claude/settings.json 의 statusLine 을 이 경로로 배선한다.
input=$(cat)

# node 가 ctx 문자열 / 작업경로 / 모델명을 탭 구분으로 출력 → shell 이 분해.
parsed=$(printf '%s' "$input" | node -e '
  let raw = "";
  process.stdin.on("data", d => raw += d);
  process.stdin.on("end", () => {
    let d = {};
    try { d = JSON.parse(raw); } catch (e) {}
    const pct = d.context_window && d.context_window.used_percentage;
    const ctx = (typeof pct === "number") ? "ctx:" + Math.round(pct) + "%" : "ctx:--";
    const dir = (d.workspace && d.workspace.current_dir) || d.cwd || "";
    const model = (d.model && (d.model.display_name || d.model.id)) || "";
    process.stdout.write([ctx, dir, model].join("\t"));
  });
')

# 탭 구분으로 분해 (model 은 공백 포함 가능 → 마지막 필드로 받음)
IFS=$(printf '\t')
read -r ctx_str cur_dir model <<EOF
$parsed
EOF
unset IFS

# 작업 경로 fallback
[ -z "$cur_dir" ] && cur_dir="$(pwd)"

# Git branch (없으면 short hash, 그것도 없으면 --)
branch=$(git -C "$cur_dir" --no-optional-locks symbolic-ref --short HEAD 2>/dev/null)
[ -z "$branch" ] && branch=$(git -C "$cur_dir" --no-optional-locks rev-parse --short HEAD 2>/dev/null)
[ -z "$branch" ] && branch="--"

# 폴더명
folder=$(basename "$cur_dir" 2>/dev/null)
[ -z "$folder" ] && folder="--"

# 모델명 fallback
[ -z "$model" ] && model="--"

printf "%s | %s | %s | %s" "$ctx_str" "$branch" "$folder" "$model"
