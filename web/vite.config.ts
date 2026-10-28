import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The built app is static files: nginx serves index.html from the release and the fingerprinted
// files under /assets/ from the shared folder.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    target: "es2022",
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8800", changeOrigin: false } },
  },
  // The browser tests run against the built app, with the API behind the same proxy.
  preview: {
    port: 4173,
    proxy: { "/api": { target: "http://127.0.0.1:8800", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test-setup.ts"],
  },
});
