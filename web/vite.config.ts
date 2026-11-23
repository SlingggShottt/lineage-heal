// Build, dev server and test settings in one file, so the app and its tests resolve `@/` alike.
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Where the API listens during development. The dashboard calls it under `/api` so that both
// share one origin: the session cookie is SameSite=Lax and is only sent to the same site (D-60).
const API_ORIGIN = process.env.VITE_API_ORIGIN ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true, // the sign-in callback URL names this port
    proxy: {
      "/api": {
        target: API_ORIGIN,
        changeOrigin: true,
        // The API has no /api prefix; production strips it the same way.
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
