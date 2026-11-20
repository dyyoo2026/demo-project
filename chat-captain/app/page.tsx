"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import type { AnalyzeErrorResponse, AnalyzeResponse } from "@/lib/schema";

const CATEGORY_STYLE: Record<string, string> = {
  공지: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  결정: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-200",
  논의중: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  질문: "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-200",
  요청: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200",
  잡담: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

const PRIORITY_STYLE: Record<string, string> = {
  높음: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
  중간: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200",
  낮음: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

const MAX_PARTICIPANTS_SHOWN = 10;

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch("/api/analyze", { method: "POST", body });
      const data = (await res.json()) as AnalyzeResponse | AnalyzeErrorResponse;
      if (!res.ok || "error" in data) {
        setError("error" in data ? data.error : "분석 중 문제가 생겼어요.");
      } else {
        setResult(data);
      }
    } catch {
      setError("서버에 연결하지 못했어요. 개발 서버가 켜져 있는지 확인해 주세요.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">방장봇</h1>
        <p className="mt-2 text-zinc-600 dark:text-zinc-400">
          카카오톡 대화 CSV를 올리면 방장처럼 요약하고, 주제를 나누고, 할 일을 정리해 줘요.
        </p>
      </header>

      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-3 rounded-xl border border-zinc-200 bg-white p-4 sm:flex-row sm:items-center dark:border-zinc-800 dark:bg-zinc-900"
      >
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="min-w-0 flex-1 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-sm file:font-medium dark:file:bg-zinc-800 dark:file:text-zinc-100"
        />
        <button
          type="submit"
          disabled={!file || loading}
          className="rounded-lg bg-zinc-900 px-5 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {loading ? "분석 중…" : "분석하기"}
        </button>
      </form>
      <p className="mt-2 text-xs text-zinc-500">
        카카오톡 대화방 → 설정 → 대화 내보내기로 만든 CSV 파일을 올려 주세요.
      </p>

      {loading && (
        <p className="mt-6 animate-pulse text-zinc-600 dark:text-zinc-400">
          방장이 대화를 읽는 중이에요. 대화가 길면 1~2분 걸릴 수 있어요.
        </p>
      )}
      {error && (
        <p className="mt-6 rounded-lg bg-red-50 p-4 text-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>
      )}
      {result && <Result result={result} />}
    </main>
  );
}

function Result({ result }: { result: AnalyzeResponse }) {
  const { stats, analysis } = result;
  const shownParticipants = stats.participants.slice(0, MAX_PARTICIPANTS_SHOWN);
  const hiddenCount = stats.participants.length - shownParticipants.length;

  return (
    <div className="mt-8 flex flex-col gap-6">
      {result.mode === "demo" && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
          <strong>데모 모드예요.</strong> API 키가 없어서 아래 분석 결과는 올린 파일과 상관없는 예시예요.
          통계는 올린 파일에서 실제로 계산했어요. <code>.env.local</code>에 <code>ANTHROPIC_API_KEY</code>를 넣고
          서버를 다시 켜면 실제 분석을 해요.
        </div>
      )}
      {result.truncated && (
        <div className="rounded-lg border border-sky-300 bg-sky-50 p-4 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-100">
          대화가 길어서 전체 {stats.totalMessages.toLocaleString()}개 중 최근{" "}
          {result.analyzedCount.toLocaleString()}개 메시지만 분석했어요.
        </div>
      )}

      <section>
        <h2 className="text-xl font-bold">{result.roomName ?? "대화방"}</h2>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Stat label="메시지" value={`${stats.totalMessages.toLocaleString()}개`} />
          <Stat label="참여자" value={`${stats.participants.length}명`} />
          <Stat label="기간" value={`${stats.start ?? "?"} ~ ${stats.end ?? "?"}`} small />
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-sm">
          {shownParticipants.map((p) => (
            <span key={p.name} className="rounded-full bg-zinc-100 px-3 py-1 dark:bg-zinc-800">
              {p.name} <span className="text-zinc-500">{p.count}</span>
            </span>
          ))}
          {hiddenCount > 0 && <span className="px-2 py-1 text-zinc-500">외 {hiddenCount}명</span>}
        </div>
      </section>

      <Section title="전체 요약">
        <p className="leading-7">{analysis.overview}</p>
      </Section>

      <Section title={`액션 아이템 (${analysis.actionItems.length})`}>
        {analysis.actionItems.length === 0 ? (
          <Empty>할 일이 없어요.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="border-b border-zinc-200 text-zinc-500 dark:border-zinc-800">
                <tr>
                  <th className="py-2 pr-3 font-medium">담당자</th>
                  <th className="py-2 pr-3 font-medium">할 일</th>
                  <th className="py-2 pr-3 font-medium">기한</th>
                  <th className="py-2 font-medium">우선순위</th>
                </tr>
              </thead>
              <tbody>
                {analysis.actionItems.map((item, i) => (
                  <tr key={i} className="border-b border-zinc-100 align-top last:border-0 dark:border-zinc-800">
                    <td className="py-3 pr-3 font-medium whitespace-nowrap">{item.assignee}</td>
                    <td className="py-3 pr-3">
                      {item.task}
                      <div className="mt-1 text-xs text-zinc-500">“{item.evidence}”</div>
                    </td>
                    <td className="py-3 pr-3 whitespace-nowrap">{item.dueDate ?? "미정"}</td>
                    <td className="py-3">
                      <Badge className={PRIORITY_STYLE[item.priority]}>{item.priority}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title={`결정 사항 (${analysis.decisions.length})`}>
        {analysis.decisions.length === 0 ? (
          <Empty>정해진 것이 없어요.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {analysis.decisions.map((d, i) => (
              <li key={i}>
                ✅ {d.content} <span className="text-sm text-zinc-500">({d.decidedBy})</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={`답이 필요한 질문 (${analysis.openQuestions.length})`}>
        {analysis.openQuestions.length === 0 ? (
          <Empty>남은 질문이 없어요.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {analysis.openQuestions.map((q, i) => (
              <li key={i}>
                ❓ {q.question} <span className="text-sm text-zinc-500">({q.askedBy})</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={`주제별 분류 (${analysis.topics.length})`}>
        <ul className="flex flex-col gap-4">
          {analysis.topics.map((t, i) => (
            <li key={i}>
              <div className="flex items-center gap-2">
                <Badge className={CATEGORY_STYLE[t.category]}>{t.category}</Badge>
                <span className="font-semibold">{t.title}</span>
              </div>
              <p className="mt-1 text-sm leading-6">{t.summary}</p>
              <p className="mt-1 text-xs text-zinc-500">{t.participants.join(", ")}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="방장 공지" action={<CopyButton text={analysis.announcement} />}>
        <pre className="font-sans text-sm leading-6 whitespace-pre-wrap">{analysis.announcement}</pre>
      </Section>

      {result.model && <p className="text-right text-xs text-zinc-400">분석 모델: {result.model}</p>}
    </div>
  );
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-lg font-bold">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function Stat({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={small ? "mt-1 text-sm font-semibold" : "mt-1 text-2xl font-bold"}>{value}</div>
    </div>
  );
}

function Badge({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap ${className ?? ""}`}>
      {children}
    </span>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-zinc-500">{children}</p>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="rounded-lg border border-zinc-300 px-3 py-1 text-sm font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
    >
      {copied ? "복사했어요" : "복사"}
    </button>
  );
}
