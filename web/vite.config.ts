import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The engine's HTTP bridge (docs/DASHBOARD_API.md). Relative asset paths, so the
// committed build in dist/ works from whatever path the Python server mounts it on.
const API = "http://127.0.0.1:8081";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: { port: 5273, proxy: { "/api": { target: API, changeOrigin: true } } },
  preview: { port: 5274, proxy: { "/api": { target: API, changeOrigin: true } } },
  build: { outDir: "dist", emptyOutDir: true },
});
