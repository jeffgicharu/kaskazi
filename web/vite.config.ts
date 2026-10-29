import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

// The headline font, preloaded. Fonts referenced from CSS only start downloading once text that
// uses them is rendered, which for this app is after the script has run; preloaded, it arrives
// with the script, so the first headline is drawn in it. Preloading the body font as well measured
// slower (mobile LCP 2.5 s against 2.2 s): it then competes with the script for the network. Both
// use font-display: optional (src/styles/fonts.css), so a late font never reflows the page.
const PRELOAD = [/^fraunces-latin-wght-normal-.*\.woff2$/];

function preloadFonts(): Plugin {
  return {
    name: "preload-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        const files = Object.keys(ctx.bundle ?? {}).filter((f) => PRELOAD.some((p) => p.test(f.split("/").pop() ?? "")));
        const links = files.map((f) => `<link rel="preload" href="/${f}" as="font" type="font/woff2" crossorigin>`).join("\n    ");
        return html.replace("</title>", `</title>\n    ${links}`);
      },
    },
  };
}

// The built app is static files: nginx serves index.html from the release and the fingerprinted
// files under /assets/ from the shared folder.
export default defineConfig({
  plugins: [react(), preloadFonts()],
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
