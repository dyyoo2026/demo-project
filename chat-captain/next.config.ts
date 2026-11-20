import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // next dev가 AGENTS.md를 영어 블록으로 덮어쓰지 않게 해요. AGENTS.md는 한국어로 직접 관리해요.
  agentRules: false,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
