import { useEffect, useMemo, useRef, useState } from "react";

import { useMonsoon } from "../../lib/useMonsoon";

// The stage behind the landing page's headline: the kaskazi blowing in from the north-east over a
// stylised coast, with a dhow under sail. It shows a still frame first (SVG, the same flow worked
// out once, drawn just after the first paint), which is all that visitors who prefer reduced
// motion, or whose device can't spare the GPU, ever see. Elsewhere the moving version starts as
// soon as the page has loaded and fades in over the still frame within a second of arriving.

const W = 1200;
const H = 600;
// The coast runs from the upper left down to the bottom, land below it, the sea and the wind above.
const SHORE = "M-20 330 C 40 352 92 404 150 430 C 214 458 262 446 318 476 C 382 510 420 548 488 562 C 552 575 600 590 650 640";

/** A deterministic scatter, so the still frame is the same on every visit. */
function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The streaks of the still frame, by the same flow the shader uses, at one moment. */
function streaks(count: number) {
  const random = seeded(7);
  const aspect = W / H;
  const [dx, dy] = [1 / Math.hypot(1, 0.38), 0.38 / Math.hypot(1, 0.38)];
  const span = aspect * 1.45 + 0.3;
  const out: { d: string; head: [number, number]; tone: number; width: number; opacity: number; r: number }[] = [];
  // Only streaks whose heads land in the frame, so a small number fills it.
  for (let tries = 0; out.length < count && tries < count * 12; tries++) {
    const [x, y, speed, w] = [random(), random(), random(), Math.pow(random(), 1.6)];
    const trail = (0.025 + speed * 0.035) * (1.6 - w * 0.6);
    const points: [number, number][] = [];
    for (let k = 0; k <= 6; k++) {
      const s = x - trail * (1 - k / 6);
      const along = span - 2 * span * s;
      const base = (y * 2 - 1) * 1.7;
      const across = base + 0.34 * Math.sin(along * 0.7 + base * 0.9 + w * 6.283) + 0.06 * Math.sin(along * 2.6 + x * 11);
      const px = dx * along - dy * across;
      const py = dy * along + dx * across;
      const depth = 2.3 - w * 1.3;
      const qx = ((px / depth) * 1.6) / aspect;
      const qy = (py / depth) * 1.6;
      points.push([((qx + 1) / 2) * W, ((1 - qy) / 2) * H]);
    }
    const head = points[points.length - 1] ?? [-1, -1];
    if (head[0] < 0 || head[0] > W || head[1] < 0 || head[1] > H) continue;
    out.push({
      d: `M${points.map(([px, py]) => `${px.toFixed(1)} ${py.toFixed(1)}`).join(" L")}`,
      head,
      tone: random() < 0.2 ? 0 : random() < 0.5 ? 1 : 2,
      width: 0.6 + w * 0.7,
      opacity: 0.32 + w * 0.38,
      r: 0.9 + w * w * 2.2,
    });
  }
  return out;
}

/** A dhow under its lateen sail, close inshore: the wind's destination, and the brand's mark. */
function Dhow() {
  return (
    <svg className="dhow-boat" viewBox="-50 -90 200 120" aria-hidden="true" focusable="false">
      <g className="dhow-body">
        <path className="dhow-mast" d="M30 -2 L30 -78" />
        <path className="dhow-sail" d="M30 -80 C 58 -62 78 -34 84 -6 C 64 -12 44 -12 22 -8 Z" />
        <path className="dhow-hull" d="M-6 -4 C 18 2 70 2 100 -10 C 92 6 78 12 60 12 L 10 12 C 2 12 -4 4 -6 -4 Z" />
      </g>
      <path className="dhow-wake" d="M-46 18 C -16 14 14 20 44 18 S 104 14 146 18" />
    </svg>
  );
}

export function WindStage() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [drawn, setDrawn] = useState(false);
  const lines = useMemo(() => (drawn ? streaks(90) : []), [drawn]);
  // The wind parts around the deck of cards beside the headline.
  const live = useMonsoon(canvas, 1000, 450, ".deck-stack");

  // The still frame's streaks are drawn just after the first paint, so they never delay the text.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDrawn(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <>
      <div className="wind-stage" data-live={live ? "yes" : "no"} aria-hidden="true">
        <svg className="wind-still" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" focusable="false">
          <defs>
            {["ink", "accent", "saffron"].map((tone) => (
              <linearGradient key={tone} id={`streak-${tone}`} x1="1" y1="0" x2="0" y2="1">
                <stop offset="0" className={`stop-${tone}`} stopOpacity="0" />
                <stop offset="1" className={`stop-${tone}`} stopOpacity="1" />
              </linearGradient>
            ))}
          </defs>
          <g className="wind-streaks">
            {lines.map((l, i) => {
              const tone = ["ink", "accent", "saffron"][l.tone] ?? "ink";
              return (
                <g key={i} opacity={l.opacity}>
                  <path d={l.d} stroke={`url(#streak-${tone})`} strokeWidth={l.width} />
                  <circle className={`dot-${tone}`} cx={l.head[0].toFixed(1)} cy={l.head[1].toFixed(1)} r={l.r.toFixed(1)} />
                </g>
              );
            })}
          </g>
        </svg>
        <canvas ref={canvas} className="wind-live" />
        <svg className="wind-coast" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMax slice" focusable="false">
          <path className="coast-land" d={`${SHORE} L -20 640 Z`} />
          <path className="coast-shore" d={SHORE} />
          <path className="coast-depth" d={SHORE} transform="translate(46 -34)" />
          <path className="coast-depth coast-depth--far" d={SHORE} transform="translate(104 -78)" />
        </svg>
      </div>
      <Dhow />
    </>
  );
}
