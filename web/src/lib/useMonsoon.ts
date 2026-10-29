import { useEffect, useState, type RefObject } from "react";

import type { Monsoon } from "./monsoon";
import { canAnimate } from "./motion";

/**
 * Starts the moving wind (./monsoon, its own small chunk) on `canvas` as soon as the page has
 * loaded, where the visitor and the device can take it, and stops it if they turn motion off.
 * `particles` is the count on a wide screen and on a phone; `parts` is a selector for an element in
 * the same section that the wind blows around. Returns whether it is drawing, so it can fade in.
 */
export function useMonsoon(canvas: RefObject<HTMLCanvasElement | null>, wide: number, narrow: number, parts?: string): boolean {
  const [live, setLive] = useState(false);

  useEffect(() => {
    if (!canAnimate()) return;
    let monsoon: Monsoon | null = null;
    let cancelled = false;
    let idle = 0;
    const halt = () => {
      monsoon?.stop();
      monsoon = null;
      setLive(false);
    };
    // The chunk is fetched straight away, beside the page; the wind starts in the first idle moment
    // after the page has loaded, so it never holds up the text.
    const loading = import("./monsoon");
    const begin = () => {
      loading
        .then(({ startMonsoon }) => {
          const el = canvas.current;
          if (cancelled || !el) return;
          const count = window.matchMedia("(max-width: 720px)").matches ? narrow : wide;
          const around = parts ? () => el.parentElement?.closest("section")?.querySelector(parts)?.getBoundingClientRect() ?? null : null;
          monsoon = startMonsoon(el, count, around, () => setLive(true), halt);
        })
        .catch(() => undefined);
    };
    const schedule = () => {
      idle = window.requestIdleCallback ? window.requestIdleCallback(begin, { timeout: 300 }) : window.setTimeout(begin, 30);
    };
    if (document.readyState === "complete") schedule();
    else window.addEventListener("load", schedule, { once: true });
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onMotion = () => {
      if (motion.matches) halt();
    };
    motion.addEventListener("change", onMotion);
    return () => {
      cancelled = true;
      window.removeEventListener("load", schedule);
      motion.removeEventListener("change", onMotion);
      if (window.cancelIdleCallback) window.cancelIdleCallback(idle);
      window.clearTimeout(idle);
      monsoon?.stop();
    };
  }, [canvas, wide, narrow, parts]);

  return live;
}
