import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 容器內 API 服務名為 api；本機直跑則 fallback localhost
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Docker bind mount（尤其 Windows/WSL）原生檔案事件常收不到 → 改用輪詢
    // 監看，否則改前端原始碼 HMR 不觸發、須手動重啟 web 容器。
    watch: { usePolling: true, interval: 300 },
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/healthz": { target: apiTarget, changeOrigin: true },
    },
  },
});
