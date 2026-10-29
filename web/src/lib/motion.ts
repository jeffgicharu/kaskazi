// What the visitor's settings and device allow in the way of motion, for everything on the site that
// moves by itself. Transitions and short entrances are plain CSS, and the reduced-motion rule in
// base.css stills them; these are for the motion that runs in script.

/** Whether the visitor has asked for less motion. */
export function prefersStill(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Whether this visitor gets the moving wind: motion welcome, data not saved, a capable device. */
export function canAnimate(): boolean {
  if (prefersStill()) return false;
  const nav = navigator as Navigator & { connection?: { saveData?: boolean }; deviceMemory?: number };
  if (nav.connection?.saveData) return false;
  if (nav.deviceMemory !== undefined && nav.deviceMemory < 4) return false;
  if (nav.hardwareConcurrency !== undefined && nav.hardwareConcurrency < 4) return false;
  return true;
}
