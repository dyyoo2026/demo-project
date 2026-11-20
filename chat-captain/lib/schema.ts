import { z } from "zod";
import type { ChatStats } from "./kakaoCsv";

export const CATEGORIES = ["공지", "결정", "논의중", "질문", "요청", "잡담"] as const;
export const PRIORITIES = ["높음", "중간", "낮음"] as const;

export const AnalysisSchema = z.object({
  overview: z.string().describe("대화 전체 요약. 3~5문장."),
  topics: z
    .array(
      z.object({
        title: z.string().describe("주제 이름. 짧게."),
        category: z.enum(CATEGORIES),
        summary: z.string().describe("이 주제에서 오간 내용 1~2문장"),
        participants: z.array(z.string()).describe("이 주제에 참여한 사람 이름"),
      }),
    )
    .describe("대화를 주제별로 묶은 목록"),
  decisions: z.array(
    z.object({
      content: z.string().describe("정해진 내용"),
      decidedBy: z.string().describe("결정한 사람. 분명하지 않으면 미정"),
    }),
  ),
  actionItems: z.array(
    z.object({
      task: z.string().describe("해야 할 일. 동사로 끝나게."),
      assignee: z.string().describe("담당자 이름. 분명하지 않으면 미정, 알림 채널이면 나"),
      dueDate: z
        .string()
        .nullable()
        .describe("기한 YYYY-MM-DD. 언급되지 않았으면 null"),
      priority: z.enum(PRIORITIES),
      evidence: z.string().describe("근거 메시지 인용. '보낸사람: 내용' 형태로 짧게"),
    }),
  ),
  openQuestions: z.array(
    z.object({
      question: z.string().describe("아직 답이 없는 질문"),
      askedBy: z.string(),
    }),
  ),
  announcement: z
    .string()
    .describe("방장이 대화방에 올릴 공지 메시지. 알림 채널이면 나에게 보내는 할 일 정리"),
});

export type Analysis = z.infer<typeof AnalysisSchema>;

export type AnalyzeResponse = {
  mode: "live" | "demo";
  roomName: string | null;
  stats: ChatStats;
  /** Claude에게 보낸(데모 모드에서는 보냈을) 메시지 수 */
  analyzedCount: number;
  truncated: boolean;
  model: string | null;
  analysis: Analysis;
};

export type AnalyzeErrorResponse = { error: string };
