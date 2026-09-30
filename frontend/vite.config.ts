import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 서버(npm run dev)는 /api를 로컬 Django(8000)로 넘긴다. 배포에서는 nginx가 같은 일을 한다.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
