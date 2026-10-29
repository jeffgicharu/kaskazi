import { Fragment, type ReactNode } from "react";

import type { Segment } from "../api/types";

// The assistant writes a small subset of Markdown: **bold**, lines starting with "- ", and line
// breaks. It is turned into React elements here, so nothing a model writes is ever treated as
// HTML: a "<script>" in an answer is shown as text.
//
// Citations arrive per segment. They are carried through the text as private-use markers
// (U+E000 numbers U+E001) and rendered by `renderCite` wherever they land.

const OPEN = "";
const CLOSE = "";
const TOKEN = new RegExp(`(\\*\\*[^*]+\\*\\*|${OPEN}[\\d,]+${CLOSE})`, "g");

export function joinSegments(segments: Segment[]): string {
  const pieces = segments.map((s) =>
    s.cites.length ? `${s.text.replace(/\s+$/, "")}${OPEN}${s.cites.join(",")}${CLOSE}${s.text.match(/\s+$/)?.[0] ?? ""}` : s.text,
  );
  // A model sometimes ends one segment with a sentence and starts the next without a space
  // ("that day." then "Branches are…"), which would read "that day.¹Branches".
  return pieces.reduce((joined, piece, i) => {
    const sentenceEnded = /[.!?:;]\s*$/.test(segments[i - 1]?.text ?? "") && !/\s$/.test(joined);
    return joined + (i > 0 && sentenceEnded && /^[^\s.,;:!?)]/.test(piece) ? " " : "") + piece;
  }, "");
}

type CiteRenderer = (numbers: number[], key: string) => ReactNode;

function inline(text: string, key: string, renderCite?: CiteRenderer): ReactNode[] {
  return text.split(TOKEN).map((part, i) => {
    const k = `${key}-${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={k}>{inline(part.slice(2, -2), k, renderCite)}</strong>;
    }
    if (part.startsWith(OPEN) && part.endsWith(CLOSE)) {
      const numbers = part.slice(1, -1).split(",").map(Number);
      return renderCite ? <Fragment key={k}>{renderCite(numbers, k)}</Fragment> : null;
    }
    return part ? <Fragment key={k}>{part}</Fragment> : null;
  });
}

export function RichText({ text, renderCite }: { text: string; renderCite?: CiteRenderer }) {
  const blocks = text.replace(/\r/g, "").split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, b) => {
        const lines = block.split("\n").filter((l) => l.trim() !== "");
        if (lines.length === 0) return null;
        const isList = lines.every((l) => /^\s*[-•]\s+/.test(l));
        if (isList) {
          return (
            <ul key={b}>
              {lines.map((l, i) => (
                <li key={i}>{inline(l.replace(/^\s*[-•]\s+/, ""), `${b}-${i}`, renderCite)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={b}>
            {lines.map((l, i) => (
              <Fragment key={i}>
                {i > 0 ? <br /> : null}
                {inline(l, `${b}-${i}`, renderCite)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </>
  );
}
