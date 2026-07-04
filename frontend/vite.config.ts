import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 容器內 API 服務名為 api；本機直跑則 fallback localhost
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/healthz": { target: apiTarget, changeOrigin: true },
    },
  },
});
