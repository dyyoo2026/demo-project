import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "방장봇",
  description: "카카오톡 대화 CSV를 요약하고 할 일을 정리해 주는 프로토타입",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
