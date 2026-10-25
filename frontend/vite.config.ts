import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: run the backend with `python -m medialab --dev` (port 8765, token "dev").
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", ws: true, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    chunkSizeWarningLimit: 900,
  },
});
