// The web app's size budget, checked on the built files (gzip, as sent). The release build fails if
// a budget is exceeded, so a heavy dependency or an eager import can't slip in unnoticed.
//
//   node scripts/bundle-budget.mjs        (after `pnpm build`)

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const DIST = new URL("../dist/", import.meta.url).pathname;
const KB = 1024;

// Measured on 29 October 2026, with room to grow: the first visit's script (React, the router,
// React Query and the landing page) 115 KB, the moving wind 2.7 KB, the styles 10 KB.
const BUDGETS = [
  { what: "the first visit's script", match: (f, entry) => f === entry, max: 125 * KB },
  { what: "the moving wind (loaded later)", match: (f) => /^monsoon-.*\.js$/.test(f), max: 6 * KB },
  { what: "each app loaded on demand", match: (f, entry) => f.endsWith(".js") && f !== entry && !f.startsWith("monsoon-"), max: 20 * KB },
  { what: "the styles", match: (f) => f.endsWith(".css"), max: 14 * KB },
];

const html = readFileSync(join(DIST, "index.html"), "utf8");
const entry = html.match(/<script[^>]+src="\/assets\/([^"]+\.js)"/)?.[1];
if (!entry) {
  console.error("No entry script found in dist/index.html.");
  process.exit(1);
}

const files = readdirSync(join(DIST, "assets")).filter((f) => f.endsWith(".js") || f.endsWith(".css"));
let failed = false;
for (const budget of BUDGETS) {
  for (const f of files.filter((name) => budget.match(name, entry))) {
    const size = gzipSync(readFileSync(join(DIST, "assets", f)), { level: 9 }).length;
    const ok = size <= budget.max;
    failed ||= !ok;
    console.log(`${ok ? "ok  " : "OVER"} ${(size / KB).toFixed(1).padStart(6)} KB of ${budget.max / KB} KB  ${budget.what}: ${f}`);
  }
}
process.exit(failed ? 1 : 0);
