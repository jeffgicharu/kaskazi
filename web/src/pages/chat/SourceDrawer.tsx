import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, type ReactNode } from "react";

import { api } from "../../api/client";
import type { Source, SourceDocument } from "../../api/types";
import { RichText } from "../../lib/richText";

/** The bank's document a citation points to, with the cited passage shown first. */
export function SourceDrawer({ source, onClose }: { source: Source; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const doc = useQuery({
    queryKey: ["source", source.document_id, source.chunk_id],
    queryFn: () =>
      api.get<SourceDocument>(`/api/sources/${encodeURIComponent(source.document_id)}?chunk=${encodeURIComponent(source.chunk_id)}`),
  });

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && panel.current) {
        const focusable = panel.current.querySelectorAll<HTMLElement>("button, a[href], [tabindex='0']");
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <div
        ref={panel}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-head">
          <div>
            <p className="eyebrow">Source {source.n}</p>
            <h2 id="source-title">{source.title}</h2>
          </div>
          <button ref={closeButton} type="button" className="icon-btn" onClick={onClose} aria-label="Close the source">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="drawer-body" tabIndex={0}>
          <section className="cited" aria-label="The passage cited">
            <p className="cited-label">Passage cited · {source.section}</p>
            <RichText text={doc.data?.highlight ?? source.quote} />
          </section>
          {doc.isLoading ? <p className="muted">Loading the document…</p> : null}
          {doc.isError ? <p className="field-error">The document couldn't be loaded.</p> : null}
          {doc.data ? (
            <article className="document">
              <p className="document-meta muted">
                {doc.data.owner} · version {doc.data.version} · effective {doc.data.effective}
              </p>
              <DocumentBody body={doc.data.body} />
            </article>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** The bank's documents: headings, paragraphs, lists and tables, rendered as elements. */
function DocumentBody({ body }: { body: string }) {
  const blocks = body.split(/\n{2,}/);
  const out: ReactNode[] = [];
  blocks.forEach((block, i) => {
    const lines = block.split("\n");
    const first = lines[0] ?? "";
    if (/^#\s/.test(first)) return; // the title is shown above
    if (/^###\s/.test(first)) out.push(<h4 key={i}>{first.slice(4)}</h4>);
    else if (/^##\s/.test(first)) out.push(<h3 key={i}>{first.slice(3)}</h3>);
    else if (lines.every((l) => l.trim().startsWith("|"))) {
      const rows = lines.filter((l) => !/^\s*\|\s*-+/.test(l)).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      const [head, ...rest] = rows;
      out.push(
        <div key={i} className="table-wrap">
          <table>
            <thead>
              <tr>{head?.map((c, j) => <th key={j} scope="col"><RichInline text={c} /></th>)}</tr>
            </thead>
            <tbody>
              {rest.map((r, k) => (
                <tr key={k}>{r.map((c, j) => <td key={j}><RichInline text={c} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else {
      const heading = /^#{2,3}\s/.test(first);
      out.push(<RichText key={i} text={heading ? lines.slice(1).join("\n") : block} />);
    }
  });
  return <>{out}</>;
}

function RichInline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
        p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <Fragment key={i}>{p}</Fragment>,
      )}
    </>
  );
}
