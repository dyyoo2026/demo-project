import Papa from "papaparse";

export type ChatMessage = {
  date: string;
  user: string;
  text: string;
};

export type ChatStats = {
  totalMessages: number;
  participants: { name: string; count: number }[];
  start: string | null;
  end: string | null;
  skippedSystem: number;
};

export type ParsedChat = {
  roomName: string | null;
  messages: ChatMessage[];
  stats: ChatStats;
};

/** 사용자에게 그대로 보여줄 수 있는 CSV 형식 오류예요. */
export class CsvFormatError extends Error {}

const DATE_HEADERS = ["date", "날짜", "일시", "시간"];
const USER_HEADERS = ["user", "name", "sender", "사용자", "이름", "보낸사람", "작성자"];
const MESSAGE_HEADERS = ["message", "text", "content", "메시지", "내용", "대화"];

// 입장, 퇴장, 초대 같은 카카오톡 시스템 메시지예요.
const SYSTEM_MESSAGE =
  /님[이을] .*(들어왔습니다|나갔습니다|초대했습니다|내보냈습니다)\.?$|^(삭제된 메시지입니다|채팅방 관리자가 메시지를 가렸습니다)\.?$/;

const LOOKS_LIKE_DATE = /^\d{4}[-./]\s?\d{1,2}[-./]\s?\d{1,2}/;

/** 카카오톡은 UTF-8로 내보내지만, Windows에서 다시 저장한 파일은 EUC-KR일 수 있어요. */
export function decodeCsv(buffer: ArrayBuffer): string {
  const utf8 = new TextDecoder("utf-8").decode(buffer);
  if (!utf8.includes("�")) return utf8;
  try {
    return new TextDecoder("euc-kr").decode(buffer);
  } catch {
    return utf8;
  }
}

/** `KakaoTalk_Chat_<방 이름>_2026-11-20-16-47-41.csv` 에서 방 이름을 뽑아요. */
export function roomNameFromFilename(filename: string): string | null {
  const match = filename
    .normalize("NFC")
    .match(/^KakaoTalk_Chat_(.+)_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.csv$/i);
  return match ? match[1] : null;
}

function findColumn(header: string[], candidates: string[]): number {
  const normalized = header.map((h) => h.trim().toLowerCase().replace(/\s+/g, ""));
  return normalized.findIndex((h) => candidates.includes(h));
}

export function parseKakaoCsv(text: string, filename = ""): ParsedChat {
  const result = Papa.parse<string[]>(text.replace(/^﻿/, ""), {
    skipEmptyLines: "greedy",
  });

  const quoteError = result.errors.find((e) => e.type === "Quotes");
  if (quoteError) {
    throw new CsvFormatError(
      `CSV의 따옴표가 맞지 않아요 (${quoteError.row ?? "?"}번째 줄 근처). 카카오톡에서 다시 내보내 주세요.`,
    );
  }

  const rows = result.data;
  if (rows.length === 0) {
    throw new CsvFormatError("파일이 비어 있어요.");
  }

  // 헤더에서 열 위치를 찾고, 못 찾으면 앞의 세 열을 날짜, 보낸 사람, 메시지로 봐요.
  const header = rows[0];
  let dateCol = findColumn(header, DATE_HEADERS);
  let userCol = findColumn(header, USER_HEADERS);
  let messageCol = findColumn(header, MESSAGE_HEADERS);
  let dataRows = rows.slice(1);

  if (dateCol < 0 || userCol < 0 || messageCol < 0) {
    if (header.length < 3) {
      throw new CsvFormatError(
        "날짜, 보낸 사람, 메시지 열을 찾지 못했어요. 카카오톡 '대화 내보내기'로 만든 CSV인지 확인해 주세요.",
      );
    }
    [dateCol, userCol, messageCol] = [0, 1, 2];
    if (LOOKS_LIKE_DATE.test(header[0] ?? "")) {
      dataRows = rows; // 헤더 없이 바로 데이터가 시작하는 파일
    }
  }

  const messages: ChatMessage[] = [];
  let skippedSystem = 0;

  for (const row of dataRows) {
    const date = (row[dateCol] ?? "").trim();
    const user = (row[userCol] ?? "").trim();
    const messageText = (row[messageCol] ?? "").trim();
    if (!messageText) continue;
    if (!user || SYSTEM_MESSAGE.test(messageText)) {
      skippedSystem++;
      continue;
    }
    messages.push({ date, user, text: messageText });
  }

  if (messages.length === 0) {
    throw new CsvFormatError("분석할 메시지가 없어요. 대화 내용이 들어 있는 CSV인지 확인해 주세요.");
  }

  const counts = new Map<string, number>();
  for (const m of messages) counts.set(m.user, (counts.get(m.user) ?? 0) + 1);
  const participants = [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);

  return {
    roomName: roomNameFromFilename(filename),
    messages,
    stats: {
      totalMessages: messages.length,
      participants,
      start: messages[0].date || null,
      end: messages[messages.length - 1].date || null,
      skippedSystem,
    },
  };
}
