import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { AssistantDisplay, Source, ThreadMessage } from "../../api/types";
import { Mark } from "../../components/Brand";
import { time } from "../../lib/format";
import { RichText, joinSegments } from "../../lib/richText";
import { CardView } from "./Cards";

type Props = {
  message: ThreadMessage;
  conversationId: string;
  onOpenSource: (source: Source) => void;
  onAskForPerson: () => void;
};

export function MessageView({ message, conversationId, onOpenSource, onAskForPerson }: Props) {
  switch (message.author) {
    case "customer":
      return (
        <li className="msg msg--customer">
          <div className="bubble bubble--customer">
            <RichText text={message.display.text} />
          </div>
          <span className="msg-time">{time(message.at)}</span>
        </li>
      );
    case "assistant":
      return (
        <AssistantMessage
          id={message.id}
          at={message.at}
          display={message.display}
          rating={message.rating}
          conversationId={conversationId}
          onOpenSource={onOpenSource}
          onAskForPerson={onAskForPerson}
        />
      );
    case "agent":
      return (
        <li className="msg msg--agent">
          <p className="msg-author">
            <span className="agent-dot" aria-hidden="true" /> {message.display.from}
          </p>
          <div className="bubble bubble--agent">
            <RichText text={message.display.text} />
          </div>
          <span className="msg-time">{time(message.at)}</span>
        </li>
      );
    case "bank":
      return (
        <li
          className={`msg msg--bank${message.display.ok === false ? " msg--bank-bad" : ""}${message.display.type === "quick_action" ? " msg--bank-neutral" : ""}`}
        >
          <p className="bank-notice">{message.display.text}</p>
          {message.display.cards?.map((c) => (
            <CardView key={"action_id" in c ? c.action_id : c.type} card={c} conversationId={conversationId} />
          ))}
        </li>
      );
    default:
      return null;
  }
}

function AssistantMessage({
  id,
  at,
  display,
  rating,
  conversationId,
  onOpenSource,
  onAskForPerson,
}: {
  id: number;
  at: string;
  display: AssistantDisplay;
  rating: -1 | 1 | null;
  conversationId: string;
  onOpenSource: (s: Source) => void;
  onAskForPerson: () => void;
}) {
  const byNumber = new Map(display.sources.map((s) => [s.n, s]));
  return (
    <li className="msg msg--assistant">
      <p className="msg-author">
        <Mark size={18} /> Kaskazi assistant
      </p>
      <div className="bubble bubble--assistant">
        {display.notice ? <p className="notice">{display.notice}</p> : null}
        <RichText
          text={joinSegments(display.segments)}
          renderCite={(numbers, key) => (
            <span className="cites" key={key}>
              {numbers.map((n) => {
                const s = byNumber.get(n);
                return s ? (
                  <button key={n} type="button" className="cite" onClick={() => onOpenSource(s)} aria-label={`Source ${n}: ${s.title}, ${s.section}`}>
                    {n}
                  </button>
                ) : null;
              })}
            </span>
          )}
        />
        {display.cards.map((c, i) => (
          <CardView key={"action_id" in c ? c.action_id : `${c.type}-${i}`} card={c} conversationId={conversationId} />
        ))}
        {display.sources.length ? (
          <div className="sources">
            <p className="sources-label">From the bank's documents</p>
            <ol>
              {display.sources.map((s) => (
                <li key={s.n}>
                  <button type="button" className="source-link" onClick={() => onOpenSource(s)}>
                    <span className="source-n" aria-hidden="true">{s.n}</span>
                    <span>
                      {s.title} <span className="muted">· {s.section}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {display.offer_handover && !display.handover ? (
          <button type="button" className="btn btn-quiet btn-small" onClick={onAskForPerson}>
            Talk to a person
          </button>
        ) : null}
      </div>
      <div className="msg-foot">
        <span className="msg-time">{time(at)}</span>
        <Feedback messageId={id} initial={rating} />
      </div>
    </li>
  );
}

const REASONS = [
  ["wrong", "Wrong"],
  ["unclear", "Unclear"],
  ["not_helpful", "Didn't help"],
  ["unsafe", "Shouldn't have said that"],
] as const;

function Feedback({ messageId, initial }: { messageId: number; initial: -1 | 1 | null }) {
  const [rating, setRating] = useState(initial);
  const [askReason, setAskReason] = useState(false);
  const send = useMutation({
    mutationFn: (body: { rating: -1 | 1; reason?: string }) => api.post("/api/feedback", { message_id: messageId, ...body }),
  });
  const rate = (value: -1 | 1, reason?: string) => {
    setRating(value);
    setAskReason(value === -1 && !reason);
    send.mutate({ rating: value, ...(reason ? { reason } : {}) });
  };
  return (
    <div className="feedback">
      <span className="visually-hidden" role="status">
        {send.isSuccess ? "Thanks, your rating is saved." : ""}
      </span>
      <button type="button" className={`icon-btn${rating === 1 ? " is-on" : ""}`} aria-pressed={rating === 1} onClick={() => rate(1)} aria-label="This answer helped">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M7 11v9H4v-9h3zm2 9h8.2a2 2 0 0 0 2-1.6l1.2-6A2 2 0 0 0 18.4 10H14l.7-3.6A2 2 0 0 0 12.8 4L9 11v9z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        </svg>
      </button>
      <button type="button" className={`icon-btn${rating === -1 ? " is-on" : ""}`} aria-pressed={rating === -1} onClick={() => rate(-1)} aria-label="This answer didn't help">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M17 13V4h3v9h-3zm-2-9H6.8a2 2 0 0 0-2 1.6l-1.2 6A2 2 0 0 0 5.6 14H10l-.7 3.6A2 2 0 0 0 11.2 20L15 13V4z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        </svg>
      </button>
      {askReason ? (
        <div className="reason-picker" role="group" aria-label="What was wrong with it?">
          {REASONS.map(([key, label]) => (
            <button key={key} type="button" className="chip chip-button" onClick={() => rate(-1, key)}>
              {label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
