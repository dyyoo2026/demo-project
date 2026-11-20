# AGENTS.md

AI 코딩 에이전트(Claude Code 등)가 이 프로젝트에서 작업할 때 읽는 안내예요.

## 이 Next.js는 학습 데이터 속 Next.js와 달라요

이 프로젝트는 Next.js 16.4를 써요. API, 관례, 파일 구조가 예전 버전과 다를 수 있어요.
코드를 쓰기 전에 `node_modules/next/dist/docs/`에서 관련 문서를 먼저 읽고, deprecated 안내를 따르세요.

## 이 파일을 관리하는 방법

원래는 `next dev`가 이 파일에 영어 안내 블록을 자동으로 쓰고, 고쳐도 다시 영어로 덮어써요.
한국어로 유지하려고 `next.config.ts`에 `agentRules: false`를 넣어 그 기능을 껐어요.

그래서 Next.js 버전을 올리면 위 안내가 여전히 맞는지 직접 확인해야 해요. 설정 설명은
`node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/agentRules.md`에 있어요.
