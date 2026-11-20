# 방장봇 작업 절차

연습용 MVP예요. 1장은 지금까지 한 일, 2장은 API 키를 넣고 실제 분석을 켜는 절차예요.

현재 상태 (2026-11-20): 데모 모드까지 완성하고 검증했어요. **API 키는 아직 넣지 않았고,
실제 Claude 호출은 한 번도 실행하지 않았어요.**

---

## 1. 지금까지 한 일

### 1-1. 프로젝트 만들기

```bash
cd ~/Desktop/Claude
npx create-next-app@latest chat-captain --ts --app --tailwind --eslint --use-npm --disable-git --yes
cd chat-captain
npm i @anthropic-ai/sdk zod papaparse
npm i -D @types/papaparse
```

설치된 버전: Next.js 16.4, React 19.3, `@anthropic-ai/sdk` 1.11, zod 4.6

> Next.js 16은 예전 버전과 API가 달라요. 코드를 고치기 전에 `AGENTS.md`의 안내대로
> `node_modules/next/dist/docs/`의 문서를 먼저 보세요.

### 1-2. 실제 카카오톡 CSV 형식 확인

실제로 내보낸 알림 채널 CSV(`KakaoTalk_Chat_<채널 이름>_<날짜>.csv`)로 확인한 형식이에요.

- 인코딩: UTF-8, 맨 앞에 BOM(`EF BB BF`)
- 헤더: `Date,User,Message`
- 날짜: `2026-11-16 09:12:04` (따옴표 없음)
- User, Message: 큰따옴표로 감쌈. 메시지 안에 줄바꿈이 들어 있음
- 파일 이름: `KakaoTalk_Chat_<방 이름>_<YYYY-MM-DD-HH-MM-SS>.csv`

이 파일에는 계좌번호 같은 개인정보가 있어서 프로젝트에 복사하지 않았어요. 대신 같은 형식의
가상 대화 `samples/KakaoTalk_Chat_프로젝트팀_2026-11-20-10-00-00.csv`를 만들었어요.

### 1-3. 파일 작성 순서

1. `samples/...csv`: 가상 팀 단톡방 대화 40줄 (입장 메시지 1줄, 여러 줄 메시지, BOM 포함)
2. `lib/kakaoCsv.ts`: CSV 파싱, 시스템 메시지 제외, 통계, 방 이름 추출, EUC-KR 대비
3. `lib/schema.ts`: 분석 결과 Zod 스키마와 응답 타입
4. `lib/analyze.ts`: 방장 프롬프트, 긴 대화 자르기, Claude 호출
5. `lib/demo.ts`: 키가 없을 때 보여줄 예시 결과
6. `app/api/analyze/route.ts`: `POST /api/analyze`
7. `app/page.tsx`, `app/layout.tsx`, `app/globals.css`: 화면
8. `README.md`, `.env.example`

### 1-4. 검증한 내용

| 확인 | 결과 |
|---|---|
| `npm run lint`, `npm run build` | 통과 |
| 가상 샘플 | 메시지 39건(입장 메시지 1건 제외), 참여자 5명, 방 이름 "프로젝트팀" |
| 실제 알림 채널 파일 (보내는 사람 1명) | 메시지 수, 참여자 1명, 기간, 방 이름이 파일 내용과 일치 |
| 6,000건짜리 큰 방 | 최근 2,358건만 보내고 `truncated: true` |
| EUC-KR로 저장한 파일 | 정상으로 읽음 |
| 빈 파일, 열 부족, 따옴표 깨짐, 파일 없음 | 한국어 오류 + HTTP 400 |
| 브라우저(헤드리스 Chrome) | 업로드 → 결과 화면 → 공지 복사까지 동작, 화면 오류 없음 |

---

## 2. 다음에 할 일: API 키를 넣고 실제 분석 켜기

### 2-1. 키 발급

1. https://console.anthropic.com 에 로그인해요.
2. 결제 수단을 등록하거나 크레딧을 충전해요. 크레딧이 없으면 호출이 실패해요.
3. API Keys → Create Key로 키를 만들어요. 키(`sk-ant-...`)는 이때 한 번만 보여요.

### 2-2. 키 넣기

```bash
cd ~/Desktop/Claude/chat-captain
cp .env.example .env.local
```

`.env.local`을 열어 다음처럼 넣어요. 따옴표나 앞뒤 공백 없이 써요.

```
ANTHROPIC_API_KEY=sk-ant-...
```

- `.env.local`은 `.gitignore`에 들어 있어서 git에 올라가지 않아요. 키를 채팅, 코드, 스크린샷에 붙이지 마세요.
- 키를 셸에 `export`하는 대신 `.env.local`을 쓰는 이유는 이 앱에서만 쓰기 위해서예요.

### 2-3. 서버 다시 켜기

`.env.local`은 서버가 켜질 때 읽어요. 켜져 있던 서버는 끄고(Ctrl+C) 다시 켜요.

```bash
npm run dev
```

### 2-4. 가상 샘플로 확인 (먼저)

```bash
curl -s -F "file=@samples/KakaoTalk_Chat_프로젝트팀_2026-11-20-10-00-00.csv" \
  http://localhost:3000/api/analyze | python3 -m json.tool | head -40
```

1~2분 걸릴 수 있어요. 비용은 1센트 안팎이에요. 다음을 확인해요.

- [ ] `"mode": "live"`이고 `"model"`에 모델 이름이 있어요 (데모면 `"demo"`, `null`)
- [ ] 액션 아이템의 담당자와 기한이 대화 내용과 맞아요
  (예: 박지훈 → 엑셀 내보내기 배포, 최유나 → 소개 자료 초안)
- [ ] `evidence`에 인용된 메시지가 실제로 샘플에 있어요 (지어낸 내용이 없어요)
- [ ] 이미 끝난 일(로그인 500 에러 수정)은 액션 아이템에 없어요
- [ ] 답이 없는 질문에 "고객사 참석 인원"이 있어요
- [ ] 방장 공지가 단톡방에 바로 붙여 넣을 만한 형태예요

### 2-5. 실제 파일로 확인

**주의:** 실제 분석을 하면 대화 내용(계좌번호 포함)이 Anthropic API로 전송돼요.

브라우저에서 http://localhost:3000 을 열고 실제 알림 채널 파일을 올려요.

- [ ] 데모 모드 배너가 없고, 맨 아래에 "분석 모델: ..."이 보여요
- [ ] 알림 채널로 인식해서 담당자가 "나"로 나와요
- [ ] 광고성 정보 수신동의 확인처럼 내가 처리할 일만 액션 아이템으로 나오고, 단순 안내는 빠져 있어요
- [ ] "방장 공지" 칸에 나에게 보내는 할 일 정리가 나와요

### 2-6. 결과가 마음에 안 들면

프롬프트는 `lib/analyze.ts`의 `SYSTEM_PROMPT`, 결과 항목은 `lib/schema.ts`에 있어요.
하나씩 바꾸고 같은 파일로 다시 돌려서 비교해요.

---

## 3. 문제가 생기면

| 증상 | 확인할 것 |
|---|---|
| 계속 데모 모드로 나와요 | `.env.local`이 `chat-captain/` 바로 아래에 있는지, 변수 이름이 `ANTHROPIC_API_KEY`인지, 서버를 다시 켰는지 |
| "API 키가 올바르지 않아요" (401) | 키를 빠짐없이 복사했는지, 앞뒤 공백이나 따옴표가 없는지 |
| "요청이 너무 많아요" (429) | 잠시 후 다시 시도 |
| "Claude API 오류가 났어요 (400)" | 서버 터미널에 찍힌 오류 메시지를 확인해요. 크레딧 부족일 수 있어요. 아니면 아래 참고 |
| "분석 결과가 너무 길어서 중간에 끊겼어요" | 더 짧은 기간의 대화를 올려요 |
| 분석이 너무 오래 걸려요 | `lib/analyze.ts`의 `effort: "medium"`을 `"low"`로 바꿔요 |

**400 오류가 계속 나면:** `lib/analyze.ts`에서 거절 시 다른 모델로 재시도하는 beta 기능을 켜 뒀어요
(`betas: ["server-side-fallback-2026-07-01"]`, `fallbacks: "default"` 두 줄). 이 부분은 타입 검사만
통과했고, 실제 호출로는 확인하지 못했어요. 400이 이 두 줄 때문이면 두 줄을 지우고 다시 시도해요.

---

## 4. 자주 쓰는 명령

```bash
npm run dev     # 개발 서버 (http://localhost:3000)
npm run lint    # 코드 검사
npm run build   # 빌드 + 타입 검사
```
