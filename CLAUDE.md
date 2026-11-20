# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 작업 공간이 무엇인가

애플리케이션 저장소가 아니라, 사용자의 **전역 Claude Code 커스터마이징**(스킬, 서브에이전트, 훅)을 만들고 평가하는 작업 공간이에요. git 저장소이고 원격은 `origin` = https://github.com/dyyoo2026/demo-project (**공개** 저장소, 기본 브랜치 `main`)이에요.

- 공개 저장소라서 커밋 작성자는 이 저장소의 로컬 설정(`dyyoo2026 <dyyoo2026@users.noreply.github.com>`)을 써요. 비밀 값이나 개인 정보를 커밋하지 마세요.
- `claude-config/`의 심볼릭 링크는 링크 경로만 커밋돼요. 링크가 가리키는 `~/.claude/` 쪽 내용은 저장소에 올라가지 않아요.
- `.claude/precommit.json`이 `{"review": false}`라서 이 저장소에서는 커밋 게이트의 리뷰 승인 단계를 건너뛰어요(루트에 lint/build/test 설정이 없어서 검사도 사실상 없어요).

- `claude-config/` 안의 항목은 모두 `~/.claude/`를 가리키는 **심볼릭 링크**예요. 여기서 고치면 모든 세션에 즉시 적용되는 실제 전역 설정이 바뀌어요.
  - `settings.json` → `~/.claude/settings.json` (JSON이 깨지면 모든 세션의 훅과 설정이 깨져요)
  - `code-reviewer.md` → `~/.claude/agents/code-reviewer.md`
  - `review-changes/` → `~/.claude/skills/review-changes/`
- 링크되지 않았지만 함께 다루는 것:
  - `~/.claude/skills/unit-test-writer/` (SKILL.md, `references/<언어>.md`, `evals/evals.json`). `iteration-*/benchmark.json`의 `skill_path`(`/Users/colldy/Desktop/Claude/unit-test-writer`)는 지금 존재하지 않는 경로예요.
  - `~/.claude/hooks/tdd/tdd_hook.py`: `.tdd` 파일이 있는 디렉터리 아래에서만 동작하는 TDD 훅(prompt/guard/run/stop)

## 커밋 게이트 구조 (review-changes)

여러 파일이 하나의 흐름을 이뤄요.

1. `settings.json`의 PreToolUse `Bash` 훅이 `precommit_gate.py`를 실행해요. stdin의 훅 JSON에서 명령을 shlex로 나눠 `git commit`을 찾아요(`cd`, `git -C` 추적).
2. 스테이징된 diff(`git diff --cached --binary`, `-a`면 `git diff HEAD`)의 sha256 앞 16자리가 **지문**이에요.
3. 지문에 대한 검사 통과 기록이 없으면 `checks.py`가 lint → build → test를 실행해요. 명령은 `<repo>/.claude/precommit.json` → package.json scripts → Makefile → 언어 기본값(go, cargo, ruff/pytest) 순서로 찾아요.
4. `review_state.py`가 `<git-dir>/claude-review.json`에 검사 기록과 리뷰 승인을 지문 단위로 저장해요. 다시 스테이징하면 지문이 바뀌어 승인이 무효가 돼요.
5. 막을 때는 exit 2와 stderr 메시지로 알려요. `git add`와 `git commit`을 한 명령에서 실행하거나, 파일을 지정해 커밋(`git commit <파일>`)해도 막아요. 훅 자체에서 예외가 나면 **fail open**(exit 0 + `systemMessage`)이에요.
6. `code-reviewer` 에이전트가 `review-changes` 스킬로 리뷰하고 `review_state.py approve`를 실행해요.

함께 바꿔야 하는 것:
- 차단 메시지의 접두어 `[커밋 게이트]`는 `code-reviewer.md`와 `SKILL.md`의 description에도 트리거 문구로 들어 있어요. 바꾸면 세 곳을 모두 바꾸세요.
- `$HOME/.claude/skills/review-changes/scripts/...` 경로가 `settings.json`, `precommit_gate.py`(`APPROVE_CMD`), `code-reviewer.md`, `SKILL.md`에 하드코딩돼 있어요.
- 시간 제한은 훅 timeout(600초)보다 짧아야 해요. `checks.py`는 `TOTAL_BUDGET = 540`, `STEP_TIMEOUT = 240`이고, `tdd_hook.py`는 `MAX_TIMEOUT = 570`이에요.

끄는 방법: 저장소별로는 `.claude/precommit.json`에 `{"enabled": false}` 또는 `{"review": false}`, 세션별로는 `CLAUDE_PRECOMMIT_GATE=off`예요.

## 스크립트 실행과 검증

스크립트 자체의 테스트 모음은 없어요. `mktemp -d`로 만든 임시 git 저장소에서 확인하세요.

```bash
S=~/.claude/skills/review-changes/scripts
python3 $S/checks.py --root <repo> [--json] [--steps lint,test] [--record]
python3 $S/review_state.py status|fingerprint|approve|revoke --repo <repo> [--all] [--note "..."]

# 게이트 훅을 직접 호출하기 (exit 2 = 차단, 사유는 stderr)
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m x"},"cwd":"<repo>"}' \
  | python3 $S/precommit_gate.py; echo "exit=$?"
```

## unit-test-writer 평가 작업 공간

`unit-test-writer-workspace/`는 skill-creator 평가 구조예요.

- `fixtures/`: 네 언어로 된 원본 예제 프로젝트예요. 실행할 때마다 `iteration-N/eval-<id>-<name>/{with_skill,without_skill}/run-1/project/`로 복사돼요. 결과는 같은 run 폴더의 `outputs/`(테스트 파일 사본, `test_run.txt`, `final_message.md`, iteration-1은 `report.md`), `grading.json`, `timing.json`에 남아요. eval마다 `eval_metadata.json`, iteration마다 `benchmark.json`/`benchmark.md`가 있어요.
- **fixture의 버그는 일부러 심은 거예요.** 고치지 마세요. 평가는 운영 코드가 바뀌지 않았는지, 버그를 찾아 보고했는지, 버그 테스트를 지우지 않고 known-failure로 표시했는지를 채점해요.
  - shop-ts: 무료 배송 경계(`>=`인데 `>`로 구현) → `it.fails` 등
  - order-java: 이미 취소된 주문을 다시 환불함 → `@Disabled("BUG: ...")`
  - points-go: 유효기간 순이 아니라 `EarnedAt` 순으로 차감 → `t.Skip("BUG: ...")`
  - subscription-py: 심은 버그가 **없어요**. 버그를 지어내지 않는지 확인하는 대조군이에요.
- 평가는 기존 테스트의 관례를 따르는지도 봐요. TS는 한국어 `describe`/`it`, Java는 한국어 `@DisplayName`과 given/when/then, Go는 table-driven `t.Run`과 got/want 메시지, Python은 `conftest.py`의 `make_subscription` fixture예요.

fixture 테스트 실행 (각 프로젝트 폴더 안에서):

| 프로젝트 | 전체 | 하나만 |
|---|---|---|
| `shop-ts` (Vitest, TS) | `npx vitest run` (`npm test`는 watch 모드예요) · `npm run typecheck` | `npx vitest run src/cart/format.test.ts -t "<이름>"` |
| `subscription-py` (pytest, Python 3.9 `.venv`) | `.venv/bin/python -m pytest -v` | `.venv/bin/python -m pytest tests/test_models.py::<test>` |
| `points-go` (Go 1.22, stdlib only) | `go vet ./... && go test -count=1 -v ./...` | `go test -count=1 -run 'TestBalance/<sub>' ./...` |
| `order-java` (Java 17, JUnit 5 + Mockito + AssertJ) | `mvn -q test` | `mvn -q test -Dtest=OrderTest#<method>` |

이 문서를 쓸 때 셸 PATH에서 `go`와 `mvn`을 찾지 못했어요. 실행하기 전에 위치를 확인하세요.

## 작성 관례

- 스킬, 에이전트, 훅 메시지 등 사용자에게 보이는 글은 한국어 해요체로 쓰고, 코드 식별자는 원래대로 둬요.
- 훅은 Claude를 멈춰 세우지 않게 써요. 예외가 나면 fail open하고 그 사실을 `systemMessage`로 알리며, 대화형 프롬프트는 쓰지 않아요. 차단은 exit 2와 stderr로 해요.
