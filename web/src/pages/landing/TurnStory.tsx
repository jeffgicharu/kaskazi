import { useCallback, useEffect, useRef, useState } from "react";

// One story of the assistant, told without words in four beats: the customer's details are masked
// before any model sees them, the answer cites the bank's document, the transfer waits for a tap,
// and a person can step in. It plays twice and rests on its last frame, which shows everything at
// once; that frame is also what visitors who prefer reduced motion see. The cards are decoration
// for sighted visitors (aria-hidden); the steps underneath say the same in text.

const BEATS = [0, 700, 1900, 3100, 4300, 5700, 7300, 8700]; // ms: bubble, masked, documents, answer, card, tapped, person
const LOOP = 12000;
const PLAYS = 2;
const LAST = BEATS.length - 1;
const WAITS = BEATS.map((at, i) => (BEATS[i + 1] ?? LOOP) - at); // how long each beat holds

function prefersStill() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function TurnStory() {
  const figure = useRef<HTMLElement>(null);
  // It opens on the customer's message, so the card is never empty while it waits to be seen.
  const [beat, setBeat] = useState(() => (prefersStill() ? LAST : 1));
  const [plays, setPlays] = useState(() => (prefersStill() ? PLAYS : 0));
  const [visible, setVisible] = useState(true);

  // Advance through the beats while the story is on screen and still has plays left.
  useEffect(() => {
    if (plays >= PLAYS || !visible) return;
    const next = WAITS[beat] ?? 1000;
    const timer = window.setTimeout(() => {
      if (beat < LAST) setBeat(beat + 1);
      else if (plays + 1 < PLAYS) {
        setBeat(0);
        setPlays(plays + 1);
      } else setPlays(PLAYS);
    }, next);
    return () => window.clearTimeout(timer);
  }, [beat, plays, visible]);

  useEffect(() => {
    const el = figure.current;
    if (!el) return;
    const seen = new IntersectionObserver((entries) => setVisible(entries[0]?.isIntersecting ?? true), { threshold: 0.1 });
    seen.observe(el);
    return () => seen.disconnect();
  }, []);

  // A gentle tilt towards a fine pointer, for depth; none on touch screens or with reduced motion.
  useEffect(() => {
    const el = figure.current;
    const host = el?.closest(".hero");
    if (!el || !host || prefersStill() || !window.matchMedia("(pointer: fine)").matches) return;
    let frame = 0;
    const onMove = (e: Event) => {
      const { clientX, clientY } = e as PointerEvent;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = el.getBoundingClientRect();
        const x = (clientX - (box.left + box.width / 2)) / window.innerWidth;
        const y = (clientY - (box.top + box.height / 2)) / window.innerHeight;
        el.style.setProperty("--tilt-y", `${(x * 9).toFixed(2)}deg`);
        el.style.setProperty("--tilt-x", `${(-y * 7).toFixed(2)}deg`);
      });
    };
    const onLeave = () => {
      el.style.setProperty("--tilt-y", "0deg");
      el.style.setProperty("--tilt-x", "0deg");
    };
    host.addEventListener("pointermove", onMove, { passive: true });
    host.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  const replay = useCallback(() => {
    setBeat(0);
    setPlays(0);
  }, []);

  const at = (n: number) => (beat >= n ? " is-on" : "");
  const step = (from: number, to: number) => (plays >= PLAYS || beat >= to ? " is-done" : beat >= from ? " is-current" : "");

  return (
    <figure ref={figure} className="story" aria-labelledby="story-title">
      <div className="story-stage" aria-hidden="true">
        <div className={`story-bubble story-bubble--customer${at(1)}`}>
          Send 80k to{" "}
          <span className={`story-pii${at(2)}`}>
            <span className="story-pii-raw">Rose Kiprop</span>
            <span className="story-pii-token">[NAME_1]</span>
          </span>{" "}
          on{" "}
          <span className={`story-pii${at(2)}`}>
            <span className="story-pii-raw">0712 345 678</span>
            <span className="story-pii-token">[PHONE_1]</span>
          </span>
          <span className={`story-scan${at(2)}`} />
        </div>
        <p className={`story-note${at(2)}`}>Hidden before any model sees it</p>

        <div className={`story-docs${at(3)}`}>
          <div className="story-doc">Tariff guide</div>
          <div className="story-doc story-doc--lit">
            <span className="story-doc-title">Transfers and payments</span>
            <span className="story-doc-passage">A new recipient can receive up to KES 50,000 in the first 24 hours.</span>
          </div>
          <div className="story-doc">Frequently asked questions</div>
        </div>

        <div className={`story-bubble story-bubble--assistant${at(4)}`}>
          Rose is a new recipient, so today you can send up to KES 50,000
          <span className="story-cite">1</span>. The rest can go tomorrow.
        </div>

        <div className={`story-slip${at(5)}${beat >= 6 ? " is-done" : ""}`}>
          <p className="story-slip-kicker">Check and confirm</p>
          <p className="story-slip-title">Send KES 50,000 to Rose Kiprop</p>
          <p className="story-slip-row">
            <span>Fee</span>
            <span>KES 55</span>
          </p>
          <span className="story-slip-button">
            <span className="story-slip-confirm">Confirm</span>
            <span className="story-slip-ok">Done</span>
          </span>
        </div>

        <div className={`story-person${at(7)}`}>
          <span className="story-person-face" />
          Not sure? A colleague takes over, with the whole conversation.
        </div>
      </div>

      <figcaption className="story-caption">
        <span id="story-title" className="visually-hidden">
          One story of the assistant, in four steps:
        </span>
        <ol className="story-steps">
          <li className={`story-step${step(1, 3)}`}>Masked</li>
          <li className={`story-step${step(3, 5)}`}>Cited</li>
          <li className={`story-step${step(5, 7)}`}>Confirmed</li>
          <li className={`story-step${step(7, 8)}`}>A person, when needed</li>
        </ol>
        {plays >= PLAYS && !prefersStill() ? (
          <button type="button" className="story-replay" onClick={replay}>
            Play again
          </button>
        ) : null}
      </figcaption>
    </figure>
  );
}
