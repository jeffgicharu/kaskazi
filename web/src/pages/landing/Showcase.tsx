import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { prefersStill } from "../../lib/motion";

// A deck of cards beside the landing page's headline, each one part of Kaskazi at work: a short
// conversation with the assistant, answered from the bank's documents, a change confirmed, a payslip read, a fraud report handed to a person,
// the release measured, the turn written to the audit trail. The front card plays its steps
// quickly, then the next comes forward; it runs continuously while it can be seen, with a pause
// button (anything that moves by itself for more than five seconds needs one). Visitors who prefer
// reduced motion get the cards still, complete, and step through them with the labels. The cards
// are decoration for sighted visitors (aria-hidden); the list underneath says the same in text.

type Scene = { key: string; label: string; summary: string; beats: number[]; length: number };

// Each scene's steps, in ms from when its card comes forward, and how long the card stays.
const SCENES: Scene[] = [
  {
    key: "ask",
    label: "Ask",
    summary: "Answers come from the bank's own documents, with the source, and personal details are hidden before any model sees them.",
    beats: [200, 600, 1250, 2450, 2950, 3350, 4000, 5300, 5700, 6350],
    length: 8600,
  },
  { key: "confirm", label: "Confirm", summary: "Nothing changes an account until the customer taps to confirm.", beats: [250, 1050, 1400, 1950], length: 4000 },
  { key: "read", label: "Read", summary: "The document desk reads payslips, IDs and statements, rates each field, and checks them against the statutory rates.", beats: [250, 550, 850, 1250, 1700], length: 4200 },
  { key: "handover", label: "Hand over", summary: "Fraud, bereavement and money troubles go to a person, with a summary.", beats: [250, 900, 1550, 2150], length: 4300 },
  { key: "measure", label: "Measure", summary: "Every release that changes the AI is measured on 131 questions and must pass the gate.", beats: [250, 550, 850, 1150, 1650], length: 4100 },
  { key: "audit", label: "Audit", summary: "Every turn is written to an append-only audit trail.", beats: [200, 420, 640, 860, 1080, 1550], length: 4000 },
];

/**
 * A conversation longer than its card: every message is laid out from the start, and the thread
 * slides up (a transform, so nothing on the page moves) to keep the latest in view, like a chat.
 * It measures after the browser has painted, when the layout is already worked out, and not at all
 * before the first message, so it never adds layout work to the page's first render.
 */
function Chat({ step, children }: { step: number; children: ReactNode }) {
  const thread = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = thread.current;
    const view = el?.parentElement;
    if (!el || !view) return;
    const shown = [...el.children].filter((c): c is HTMLElement => c instanceof HTMLElement && c.classList.contains("is-on"));
    const last = shown[shown.length - 1];
    const messages = [...el.children].filter((c): c is HTMLElement => c instanceof HTMLElement);
    if (!last) {
      el.style.transform = "";
      messages.forEach((c) => c.removeAttribute("data-past"));
      return;
    }
    const shift = Math.max(0, last.offsetTop + last.offsetHeight - view.clientHeight);
    el.style.transform = `translateY(${-shift}px)`;
    // Messages that have gone up past the top of the window fade away, rather than sit half cut off.
    messages.forEach((c) => c.toggleAttribute("data-past", c.offsetTop < shift));
  }, [step]);
  return (
    <div className="deck-chat">
      <div ref={thread} className="deck-thread">
        {children}
      </div>
    </div>
  );
}

/** The assistant's reply: typing dots from step `typing`, the answer replacing them at `answer`. */
function Reply({ on, typing, answer, children }: { on: (n: number) => string; typing: number; answer: number; children: ReactNode }) {
  return (
    <div className={`deck-bubble deck-bubble--assistant${on(typing)}${on(answer) ? " is-answered" : ""}`}>
      <span className="deck-dots">
        <span />
        <span />
        <span />
      </span>
      <span className="deck-answer">{children}</span>
    </div>
  );
}

/** A card's contents; `on(n)` is " is-on" once the card has reached its nth step. */
function SceneBody({ scene, step, on }: { scene: string; step: number; on: (n: number) => string }): ReactNode {
  switch (scene) {
    case "ask":
      return (
        <>
          <p className="deck-kicker">Customer assistant</p>
          <Chat step={step}>
            <div className={`deck-bubble deck-bubble--customer${on(1)}`}>Can I send money to a mobile money number from here?</div>
            <Reply on={on} typing={2} answer={3}>
              Yes, to any mobile money wallet in Kenya. It usually arrives within moments<span className="deck-cite">1</span>.
            </Reply>
            <p className={`deck-source${on(3)}`}>
              <span className="deck-cite">1</span> Transfers and payments · Mobile money transfers
            </p>
            <div className={`deck-bubble deck-bubble--customer${on(4)}`}>
              Send 80k to{" "}
              <span className={`deck-pii${on(5)}`}>
                <span className="deck-pii-raw">Rose Kiprop</span>
                <span className="deck-pii-token">[NAME_1]</span>
              </span>{" "}
              on{" "}
              <span className={`deck-pii${on(5)}`}>
                <span className="deck-pii-raw">0700 926 613</span>
                <span className="deck-pii-token">[PHONE_1]</span>
              </span>
              <span className={`deck-scan${on(5)}`} />
            </div>
            <p className={`deck-note${on(5)}`}>Hidden before any model sees it</p>
            <Reply on={on} typing={6} answer={7}>
              Rose is a new recipient, so today you can send up to KES 50,000<span className="deck-cite">2</span>. The rest can
              go tomorrow.
            </Reply>
            <p className={`deck-source${on(7)}`}>
              <span className="deck-cite">2</span> Transfers and payments · New recipients
            </p>
            <div className={`deck-bubble deck-bubble--customer${on(8)}`}>OK, send 50k now then</div>
            <Reply on={on} typing={9} answer={10}>
              Sending KES 50,000 costs KES 55<span className="deck-cite">1</span>. I've prepared it: check the details and tap
              Confirm.
            </Reply>
          </Chat>
        </>
      );
    case "confirm":
      return (
        <>
          <p className="deck-kicker">A change waits for a tap</p>
          <div className={`deck-slip${on(1)}${on(3) ? " is-done" : ""}`}>
            <p className="deck-slip-kicker">Check and confirm</p>
            <p className="deck-slip-title">Send KES 50,000 to Rose Kiprop</p>
            <p className="deck-row">
              <span>To</span>
              <span>Rose Kiprop · mobile money</span>
            </p>
            <p className="deck-row">
              <span>Fee</span>
              <span>KES 55</span>
            </p>
            <span className={`deck-slip-button${on(2)}`}>
              <span className="deck-slip-confirm">Confirm</span>
              <span className="deck-slip-ok">Done</span>
            </span>
          </div>
          <div className={`deck-sms${on(4)}`}>
            <span className="deck-sms-from">Kaskazi</span>
            KES 50,000 sent to Rose Kiprop. Fee KES 55.
          </div>
        </>
      );
    case "read":
      return (
        <>
          <p className="deck-kicker">Document desk</p>
          <div className="deck-read">
            <div className="deck-page" aria-hidden="true">
              <span className="deck-page-title">Payslip · October 2026</span>
              {[70, 92, 55, 84, 62, 88, 48].map((w, i) => (
                <span key={i} className="deck-page-line" style={{ width: `${w}%` }} />
              ))}
            </div>
            <ul className="deck-fields">
              {[
                ["Employee", "Amina Wanjiru Kariuki"],
                ["Gross pay", "KES 97,610.00"],
                ["Net pay", "KES 68,940.13"],
              ].map(([field, value], i) => (
                <li key={field} className={`deck-field${on(i + 1)}`}>
                  <span className="deck-field-name">{field}</span>
                  <span className="deck-field-value">{value}</span>
                  <span className="deck-confidence">Clear</span>
                </li>
              ))}
            </ul>
          </div>
          <p className={`deck-check${on(4)}`}>PAYE, NSSF, SHIF and the Housing Levy match the 2026 rates</p>
          <p className={`deck-check${on(5)}`}>The net pay arrived in her statement on payday</p>
        </>
      );
    case "handover":
      return (
        <>
          <p className="deck-kicker">A person steps in</p>
          <div className={`deck-bubble deck-bubble--customer${on(1)}`}>someone took 20k from my account and it wasn't me</div>
          <div className={`deck-bubble deck-bubble--assistant${on(2)}`}>
            I'm sorry. A colleague is joining this chat now, and you can block your card straight away.
          </div>
          <div className={`deck-console${on(3)}`}>
            <p className="deck-console-head">
              <span>Agent console</span>
              <span className="deck-tag">Fraud</span>
            </p>
            <p className="deck-console-line">Reports a KES 20,000 withdrawal she didn't make. Debit card still active.</p>
            <p className={`deck-console-line deck-console-picked${on(4)}`}>Picked up by an agent · replying in the chat</p>
          </div>
        </>
      );
    case "measure":
      return (
        <>
          <p className="deck-kicker">Every release, measured</p>
          <p className="deck-measure-title">Golden set · 131 questions</p>
          {[
            ["Key fact stated", 92.5, "92.5%"],
            ["Answers grounded in a source", 96.8, "96.8%"],
            ["Unanswerable, nothing invented", 100, "11 of 11"],
            ["Changes that waited for a tap", 100, "3 of 3"],
          ].map(([name, share, shown], i) => (
            <div key={name as string} className={`deck-meter${on(i + 1)}`}>
              <p className="deck-row">
                <span>{name}</span>
                <span>{shown}</span>
              </p>
              <span className="deck-bar">
                <span style={{ "--share": (share as number) / 100 } as CSSProperties} />
              </span>
            </div>
          ))}
          <p className={`deck-pass${on(5)}`}>Passes the gate</p>
        </>
      );
    default:
      return (
        <>
          <p className="deck-kicker">Audit trail · append-only</p>
          <ol className="deck-events">
            {[
              ["screen", "no injection found"],
              ["retrieval", "3 passages"],
              ["model", "Claude Haiku 5.5"],
              ["tool", "get_cards"],
              ["output", "every check passed"],
            ].map(([kind, what], i) => (
              <li key={kind} className={`deck-event${on(i + 1)}`}>
                <span className="deck-event-kind">{kind}</span>
                <span>{what}</span>
                <span className="deck-hash">#{["a91f", "3c07", "e25b", "7d4a", "0b6e"][i]}</span>
              </li>
            ))}
          </ol>
          <p className={`deck-pass${on(6)}`}>Chain intact · nothing can be edited</p>
        </>
      );
  }
}

export function Showcase() {
  const figure = useRef<HTMLElement>(null);
  const [still] = useState(prefersStill);
  const [scene, setScene] = useState(0);
  const [beat, setBeat] = useState(() => (still ? 99 : 0));
  const [paused, setPaused] = useState(false);
  const [visible, setVisible] = useState(true);
  const current = SCENES[scene] ?? SCENES[0];

  // The front card's next step, or the next card: always, while it can be seen and isn't paused.
  useEffect(() => {
    if (still || paused || !visible || !current) return;
    const at = beat === 0 ? 0 : (current.beats[beat - 1] ?? 0);
    const next = current.beats[beat];
    const timer = window.setTimeout(
      () => {
        if (next !== undefined) setBeat(beat + 1);
        else {
          setScene((scene + 1) % SCENES.length);
          setBeat(0);
        }
      },
      next !== undefined ? next - at : current.length - at,
    );
    return () => window.clearTimeout(timer);
  }, [scene, beat, paused, visible, current, still]);

  // Not while scrolled away or in a hidden tab: it picks up where it was.
  useEffect(() => {
    const el = figure.current;
    if (!el) return;
    const seen = new IntersectionObserver((entries) => setVisible(entries[0]?.isIntersecting ?? true), { threshold: 0.1 });
    seen.observe(el);
    const onVisibility = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      seen.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // A gentle tilt towards a fine pointer, for depth; none on touch screens or with reduced motion.
  useEffect(() => {
    const el = figure.current;
    const host = el?.closest(".hero");
    if (!el || !host || still || !window.matchMedia("(pointer: fine)").matches) return;
    let frame = 0;
    const onMove = (e: Event) => {
      const { clientX, clientY } = e as PointerEvent;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = el.getBoundingClientRect();
        el.style.setProperty("--tilt-y", `${(((clientX - (box.left + box.width / 2)) / window.innerWidth) * 9).toFixed(2)}deg`);
        el.style.setProperty("--tilt-x", `${((-(clientY - (box.top + box.height / 2)) / window.innerHeight) * 7).toFixed(2)}deg`);
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
  }, [still]);

  const show = (i: number) => {
    setScene(i);
    setBeat(still ? 99 : 0);
  };

  return (
    <figure ref={figure} className="deck" aria-labelledby="deck-title" data-paused={paused || !visible ? "yes" : "no"}>
      <div className="deck-stack" aria-hidden="true">
        {SCENES.map((s, i) => {
          const pos = (i - scene + SCENES.length) % SCENES.length;
          const place = pos === 0 ? "front" : pos === 1 ? "next" : pos === 2 ? "after" : pos === SCENES.length - 1 ? "gone" : "hidden";
          const on = (n: number) => (i === scene && beat >= n ? " is-on" : "");
          return (
            <div key={s.key} className={`deck-card deck-card--${place}`}>
              <div className={`deck-body deck-body--${s.key}`}>
                <SceneBody scene={s.key} step={i === scene ? beat : 0} on={on} />
              </div>
            </div>
          );
        })}
      </div>

      <figcaption className="deck-caption">
        <span id="deck-title" className="visually-hidden">
          Kaskazi at work, in six parts:
        </span>
        <ol className="deck-steps">
          {SCENES.map((s, i) => (
            <li key={s.key}>
              <button
                type="button"
                className={`deck-step${i === scene ? " is-current" : ""}`}
                aria-current={i === scene ? "step" : undefined}
                onClick={() => show(i)}
              >
                {s.label}
                {i === scene && !still ? (
                  <span key={scene} className="deck-progress" style={{ animationDuration: `${s.length}ms` }} />
                ) : null}
              </button>
              <span className="visually-hidden">: {s.summary}</span>
            </li>
          ))}
        </ol>
        {!still ? (
          <button
            type="button"
            className={`deck-pause${paused ? " is-paused" : ""}`}
            aria-label={paused ? "Play the cards" : "Pause the cards"}
            onClick={() => setPaused(!paused)}
          />
        ) : null}
      </figcaption>
    </figure>
  );
}
