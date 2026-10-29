import { useEffect, useState, type RefObject } from "react";

/**
 * Whether an element is on screen, so decorative CSS motion can pause once it scrolls away. Even a
 * compositor animation isn't free: while anything animates, Chrome works out the page's
 * intersection observers on the main thread every frame.
 */
export function useOnScreen(ref: RefObject<Element | null>): boolean {
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const seen = new IntersectionObserver((entries) => setOnScreen(entries[0]?.isIntersecting ?? true));
    seen.observe(el);
    return () => seen.disconnect();
  }, [ref]);
  return onScreen;
}
