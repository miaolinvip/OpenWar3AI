import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

// 不用 @vitejs/plugin-react：esbuild 自己就能转 TSX（没有热更新快刷，换来零额外依赖、完全离线可装）
// 两个入口：index.html = 控制台；live.html = 直播日志页（/log，OBS 浏览器源用，不带侧栏顶栏，见 server/stream.py）
export default defineConfig({
  esbuild: { jsx: "automatic" },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8866",
      "/ws": { target: "ws://127.0.0.1:8866", ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        live: fileURLToPath(new URL("./live.html", import.meta.url)),
      },
    },
  },
});
