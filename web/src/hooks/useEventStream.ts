import { useEffect, useRef } from "react";

export type StreamEvent = { type: string; [key: string]: unknown };

/** Listens to a server-sent event stream; reconnects as the browser does, after each release. */
export function useEventStream(url: string | null, onEvent: (event: StreamEvent) => void): void {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });

  useEffect(() => {
    if (!url || typeof EventSource === "undefined") return;
    const source = new EventSource(url, { withCredentials: true });
    const types = ["progress", "done", "agent_message", "agent_done", "queue_changed"];
    const listener = (e: MessageEvent<string>) => {
      try {
        handler.current(JSON.parse(e.data) as StreamEvent);
      } catch {
        // ignore a malformed event
      }
    };
    types.forEach((t) => source.addEventListener(t, listener as EventListener));
    return () => {
      types.forEach((t) => source.removeEventListener(t, listener as EventListener));
      source.close();
    };
  }, [url]);
}
