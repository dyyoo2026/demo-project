# 방장봇 (MVP)

카카오톡 대화방을 CSV로 내보내 올리면, 방장처럼 대화를 읽고 다음을 정리해 줘요.

- 전체 요약
- 액션 아이템 (담당자, 할 일, 기한, 우선순위, 근거 메시지)
- 결정 사항, 답이 필요한 질문
- 주제별 분류 (공지, 결정, 논의중, 질문, 요청, 잡담)
- 방에 바로 붙여 넣을 방장 공지 (알림 채널이면 나에게 보내는 할 일 정리)

DB, 로그인, 저장 기능은 없어요. 올린 파일은 요청 한 번에만 쓰고 버려요.

지금까지 한 작업과 API 키를 넣는 절차는 [docs/PROCEDURE.md](docs/PROCEDURE.md)에 정리돼 있어요.

## 실행

```bash
npm install
npm run dev
```

http://localhost:3000 을 열고 CSV를 올리면 돼요. 바로 써 볼 수 있는 가상 대화가
`samples/KakaoTalk_Chat_프로젝트팀_2026-11-20-10-00-00.csv`에 있어요.

## Claude API 키

키가 없으면 **데모 모드**로 동작해요. 통계(메시지 수, 참여자, 기간)는 올린 파일에서 실제로
계산하고, 분석 결과는 예시 데이터를 보여줘요.

실제 분석을 하려면:

1. https://console.anthropic.com 에서 API 키를 발급받아요.
2. `.env.example`을 `.env.local`로 복사하고 키를 넣어요.
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. `npm run dev`를 다시 실행해요.

모델은 `claude-opus-5-5`를 써요(`lib/analyze.ts`). 대화가 길면 최근 메시지부터 약 15만 자까지만
보내고, 화면에 몇 개를 분석했는지 표시해요. 올린 대화 내용은 분석을 위해 Anthropic API로 전송돼요.

## CSV 형식

카카오톡 대화방 → 설정 → 대화 내보내기로 만든 파일이에요.

```
Date,User,Message
2026-11-16 09:12:04,"보낸 사람","메시지 (여러 줄 가능)"
```

- UTF-8(BOM 포함)과 EUC-KR 모두 읽어요.
- 헤더가 `날짜/사용자/메시지`처럼 달라도 찾고, 못 찾으면 앞의 세 열을 날짜, 보낸 사람, 메시지로 봐요.
- 입장, 퇴장 같은 시스템 메시지는 빼요.
- 파일 이름 `KakaoTalk_Chat_<방 이름>_<날짜>.csv`에서 방 이름을 가져와요.

## 파일 구조

| 파일 | 역할 |
|---|---|
| `app/page.tsx` | 업로드 화면과 결과 화면 |
| `app/api/analyze/route.ts` | `POST /api/analyze`: 파일을 받아 파싱하고 분석해요 |
| `lib/kakaoCsv.ts` | CSV 파싱과 통계 |
| `lib/analyze.ts` | 방장 프롬프트와 Claude 호출 |
| `lib/schema.ts` | 분석 결과 스키마(Zod)와 응답 타입 |
| `lib/demo.ts` | 데모 모드 예시 결과 |
