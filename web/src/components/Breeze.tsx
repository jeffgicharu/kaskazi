import { useRef } from "react";

import { useMonsoon } from "../lib/useMonsoon";

/**
 * The kaskazi behind a page's header: the landing page's wind, sparser and fading out below. It's
 * decoration, so it's hidden from assistive technology, and where the wind doesn't run (reduced
 * motion, a slow device) the header is simply plain.
 */
export function Breeze() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const live = useMonsoon(canvas, 240, 110);
  return <canvas ref={canvas} className="breeze" data-live={live ? "yes" : "no"} aria-hidden="true" />;
}
