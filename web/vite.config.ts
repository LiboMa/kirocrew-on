/// <reference types="vitest/config" />
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Development: `npm run dev` proxies the platform API (default: the 8921 browser fixture). The platform checks Host and
// Origin, so the proxy presents the target's own origin.
const api = process.env.OCTOPUS_DEV_API ?? "http://127.0.0.1:8921";
const proxy = { target: api, changeOrigin: true, headers: { Origin: api } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  build: { outDir: "../octopus/ui/dist", emptyOutDir: true, assetsDir: "assets", sourcemap: false },
  server: {
    port: 5173,
    proxy: { "/v1": proxy, "/health": proxy, "/crew-entry.html": proxy, "/crew.js": proxy, "/style.css": proxy },
  },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"], css: false },
});
