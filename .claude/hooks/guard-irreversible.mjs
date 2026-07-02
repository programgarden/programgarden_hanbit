#!/usr/bin/env node
// PreToolUse 하드 게이트 — 비가역/외부 영향 명령에 대해 permissionDecision:"ask" 를 돌려
// 사용자 확인을 강제한다. `--dangerously-skip-permissions` (skipDangerousModePermissionPrompt)
// 상태에서도 PreToolUse 의 "ask"/"deny" 는 우선권을 갖는다 (Claude Code hooks 사양).
//
// 가드 범위 = "외부로 나가는 / 되돌릴 수 없는" 케이스에 집중:
//   - GitHub 저장소/히스토리 파괴 (force-push, branch -D, repo delete, visibility 변경, release delete, api DELETE)
//   - 로컬 git 비가역 (reset --hard, clean -f, filter-branch/filter-repo)
//   - 프로덕션 배포 (vercel/netlify --prod, promote)
//   - 계정 단위 클라우드 리소스 삭제 (aws/gcloud delete)
//   - 프로젝트 밖(홈/시스템/로그인 볼륨/상위 경로) rm -rf + 저장소 자체 파괴(.git / 워크스페이스 루트)
//     ※ /workspace 안의 파일 삭제는 통과 — git 회복 가능 + 컨테이너에 갇혀 호스트 무영향
// 일부러 제외: 평범한 `git push`(정상 작업 흐름), /workspace 안 일반 파일 편집·삭제. 즉 "외부로
//   나가는 비가역" 만 잡는다. (hanbit 은 로컬 SQLite 라 클라우드 DB 가드 룰은 없다.)
//
// 패턴은 의도적으로 사람이 읽고 고치기 쉽게 둔다. 새 클라우드 CLI 를 붙이면 RULES 에 한 줄 추가.

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let input = {};
  try {
    input = JSON.parse(raw || "{}");
  } catch {
    process.exit(0); // 파싱 실패 시 게이트 통과(차단하지 않음) — 가드는 차단이 아니라 보조다
  }
  const toolName = input.tool_name || "";

  if (toolName !== "Bash") process.exit(0);
  const cmd = (input.tool_input && input.tool_input.command) || "";

  // [정규식, 사람이 읽을 사유]
  // 공통 원칙: 토큰 사이 매칭은 `[^\n;&|]*` 로 — 옵션이 끼어도(`git -c k=v push --force`) 잡되
  //   `;` `&&` `|` 같은 명령 분리자는 못 넘게 해서 `git log && curl … --force` 류 오발(false-ask)을 막는다.
  const RULES = [
    // git 플래그는 소문자 고정이라 i 불필요.
    [/\bgit\b[^\n;&|]*\bpush\b[^\n;&|]*(--force\b|--force-with-lease|\s-f\b|--mirror|--delete\b|--prune\b)/, "git force-push / 원격 branch 삭제 / mirror / prune — 원격 히스토리 비가역 변경"],
    [/\bgit\b[^\n;&|]*\bpush\b[^\n;&|]*\s\+\S+/, "git push '+refspec' — 강제 푸시(히스토리 덮어쓰기)"],
    [/\bgit\s+reset\s+--hard\b/, "git reset --hard — 로컬 변경 비가역 폐기"],
    [/\bgit\s+branch\s+-D\b/, "git branch -D — 병합 안 된 브랜치 강제 삭제"],
    [/\bgit\s+(filter-branch|filter-repo)\b/, "git 히스토리 재작성"],
    [/\bgit\s+clean\s+-[A-Za-z]*f/, "git clean -f — 추적 안 된 파일 비가역 삭제"],
    // 클라우드 룰은 하위명령/플래그 대소문자 섞임(`gh api -X delete`) 대비 i 플래그.
    [/\bgh\s+repo\s+delete\b/i, "gh repo delete — 저장소 삭제"],
    [/\bgh\s+repo\s+edit\b[^\n;&|]*--visibility/i, "gh repo edit --visibility — 공개 범위 변경(노출 위험)"],
    [/\bgh\s+release\s+delete\b/i, "gh release delete — 릴리스 삭제"],
    [/\bgh\s+api\b[^\n;&|]*(-X\s*DELETE|--method[= ]*DELETE)/i, "gh api DELETE — API 비가역 호출"],
    [/\b(vercel|netlify)\b[^\n;&|]*(--prod\b|\bpromote\b)/i, "프로덕션 배포 (vercel/netlify)"],
    [/\baws\b[^\n;&|]*\bdelete\b/i, "aws delete — 클라우드 리소스 삭제"],
    [/\baws\s+s3\s+(rb|rm)\b[^\n;&|]*(--recursive|--force)/i, "aws s3 버킷/대량 삭제"],
    [/\bgcloud\b[^\n;&|]*\bdelete\b/i, "gcloud delete — 클라우드 리소스 삭제"],
  ];

  for (const [re, reason] of RULES) {
    if (re.test(cmd)) return ask(reason, cmd);
  }

  // rm 의 강제+재귀 플래그(-rf / -fr / -Rf 등) — "프로젝트 밖으로 나가는" 삭제만 막는다.
  // /workspace 안(상대경로 포함)의 파일/디렉토리 삭제는 통과: 프로젝트 파일은 git 으로 회복 가능하고
  //   컨테이너에 갇혀 있어 호스트 맥북에 영향이 없다 (README '외부로 나가는 비가역만 잡는다').
  // 막는 것 = ① 프로젝트 밖 탈출(홈/시스템 절대경로·`/home/dev/.claude` 로그인 볼륨·`..` 상위·맨 `/`·`~`)
  //          ② 저장소 자체 파괴(.git 디렉토리 / 워크스페이스 루트 통째) — git 으로도 회복 불가.
  const rmForce = /\brm\b[^\n;&|]*\s-[A-Za-z]*r[A-Za-z]*f|\brm\b[^\n;&|]*\s-[A-Za-z]*f[A-Za-z]*r/i.test(cmd);
  if (rmForce) {
    const escapesProject =
      /(^|\s)(\/|~)(\s|$)/.test(cmd) ||                            // 맨 루트 `/` 또는 홈 `~` 단독
      /(^|\s)~\//.test(cmd) ||                                      // ~/... (홈 하위)
      /(^|\s)\.\.(\/|\s|$)/.test(cmd) ||                            // .. 상위 탈출
      /(^|\s)\/(etc|usr|bin|sbin|lib|lib64|var|home|root|boot|opt|sys|proc|dev)(\/|\s|$)/.test(cmd); // 시스템/홈 절대경로(예: /home/dev/.claude 로그인 볼륨)
    const repoNuke =
      /(^|\s)\/workspace\/?(\s|$)/.test(cmd) ||                     // /workspace 루트 통째
      /(^|[\s\/])\.git(\/|\s|$)/.test(cmd);                         // .git 디렉토리 삭제(히스토리 비가역)
    if (escapesProject) return ask("rm -rf 가 프로젝트 밖(홈/시스템/로그인 볼륨/상위 경로) 대상 — 컨테이너 밖 비가역 삭제", cmd);
    if (repoNuke) return ask("rm -rf 가 저장소 자체(.git / 워크스페이스 루트) 대상 — git 으로도 회복 불가", cmd);
  }

  process.exit(0);

  function ask(reason, command) {
    process.stdout.write(
      JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "ask",
          permissionDecisionReason: `⚠️ 비가역/외부 영향 명령 — 사용자 승인 필요: ${reason}\n  $ ${command}`,
        },
      })
    );
    process.exit(0);
  }
});
