// ~/.claude/settings.json 의 statusLine 을 repo 의 상태 줄 렌더러로 배선한다 (idempotent).
//
// ~/.claude 는 격리 named volume 이라 Dockerfile 로는 못 심는다(볼륨이 이미지 내용을 가림).
// 그래서 run-isolated.sh 의 부트스트랩이 매 진입 시 이 스크립트를 돌려, 기존/새 볼륨
// 양쪽에서 상태 줄을 자동 복원한다. 렌더러 본체는 sandbox/statusline-command.sh (단일 출처).
import fs from "node:fs";
import path from "node:path";

const dir = process.env.CLAUDE_CONFIG_DIR || `${process.env.HOME}/.claude`;
const file = path.join(dir, "settings.json");
const want = { type: "command", command: "bash /workspace/sandbox/statusline-command.sh" };

let settings = {};
try {
  settings = JSON.parse(fs.readFileSync(file, "utf8"));
} catch {
  /* 파일 없음/깨짐 → 빈 객체에서 시작 */
}

if (JSON.stringify(settings.statusLine) === JSON.stringify(want)) {
  process.exit(0); // 이미 배선됨 — no-op
}

settings.statusLine = want;
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(file, JSON.stringify(settings, null, 2) + "\n");
console.log("[sandbox] statusLine wired → sandbox/statusline-command.sh");
