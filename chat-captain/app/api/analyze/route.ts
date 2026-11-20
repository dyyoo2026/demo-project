import Anthropic from "@anthropic-ai/sdk";
import { AnalysisError, analyzeChat, buildTranscript } from "@/lib/analyze";
import { DEMO_ANALYSIS } from "@/lib/demo";
import { CsvFormatError, decodeCsv, parseKakaoCsv } from "@/lib/kakaoCsv";
import type { AnalyzeErrorResponse, AnalyzeResponse } from "@/lib/schema";

const MAX_FILE_BYTES = 20 * 1024 * 1024;

function fail(status: number, error: string) {
  return Response.json({ error } satisfies AnalyzeErrorResponse, { status });
}

export async function POST(request: Request) {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return fail(400, "파일을 받지 못했어요. CSV 파일을 다시 선택해 주세요.");
  }

  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return fail(400, "CSV 파일을 선택해 주세요.");
  }
  if (file.size > MAX_FILE_BYTES) {
    return fail(413, "파일이 너무 커요. 20MB 이하의 CSV만 올릴 수 있어요.");
  }

  let chat;
  try {
    chat = parseKakaoCsv(decodeCsv(await file.arrayBuffer()), file.name);
  } catch (error) {
    if (error instanceof CsvFormatError) return fail(400, error.message);
    throw error;
  }

  const transcript = buildTranscript(chat.messages);
  const base = {
    roomName: chat.roomName,
    stats: chat.stats,
    analyzedCount: transcript.includedCount,
    truncated: transcript.truncated,
  };

  if (!process.env.ANTHROPIC_API_KEY) {
    return Response.json({
      ...base,
      mode: "demo",
      model: null,
      analysis: DEMO_ANALYSIS,
    } satisfies AnalyzeResponse);
  }

  try {
    const { analysis, model } = await analyzeChat(chat, transcript);
    return Response.json({ ...base, mode: "live", model, analysis } satisfies AnalyzeResponse);
  } catch (error) {
    if (error instanceof AnalysisError) return fail(502, error.message);
    if (error instanceof Anthropic.AuthenticationError) {
      return fail(401, "API 키가 올바르지 않아요. .env.local의 ANTHROPIC_API_KEY를 확인해 주세요.");
    }
    if (error instanceof Anthropic.RateLimitError) {
      return fail(429, "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.");
    }
    if (error instanceof Anthropic.APIError) {
      console.error("Claude API error", error.status, error.message);
      return fail(502, `Claude API 오류가 났어요 (${error.status ?? "연결 실패"}). 잠시 후 다시 시도해 주세요.`);
    }
    console.error(error);
    return fail(500, "분석 중 예상하지 못한 문제가 생겼어요.");
  }
}
