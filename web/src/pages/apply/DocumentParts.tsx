import type { DeskDocument, DocCheck, DocField } from "../../api/types";

// What a document's status means, said plainly, with a tone that never carries meaning alone.
const STATUS: Record<DeskDocument["status"], { label: string; tone: string }> = {
  received: { label: "Received", tone: "" },
  verified: { label: "Read and checked", tone: "chip-ok" },
  needs_review: { label: "A colleague will check it", tone: "chip-warn" },
  approved: { label: "Approved by a colleague", tone: "chip-ok" },
  corrected: { label: "Corrected by a colleague", tone: "chip-ok" },
  rejected: { label: "Not accepted", tone: "chip-danger" },
  unreadable: { label: "Couldn't be read", tone: "chip-danger" },
};

export function DocStatus({ status }: { status: DeskDocument["status"] }) {
  const s = STATUS[status];
  return <span className={`chip ${s.tone}`}>{s.label}</span>;
}

// Amounts on a document are shown to the cent, as printed.
const CENTS = new Intl.NumberFormat("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MONEY_FIELDS = new Set(["basic_pay", "gross_pay", "paye", "nssf", "shif", "housing_levy", "total_deductions", "net_pay", "opening_balance", "closing_balance"]);
const DATE_FIELDS = new Set(["pay_date", "date_of_birth", "date_of_issue", "period_start", "period_end"]);

export function fieldValue(f: DocField): string {
  if (f.value == null || f.value === "") return "—";
  if (MONEY_FIELDS.has(f.name)) {
    const n = Number(f.value);
    return Number.isFinite(n) ? `KES ${CENTS.format(n)}` : f.value;
  }
  if (DATE_FIELDS.has(f.name) && /^\d{4}-\d{2}-\d{2}$/.test(f.value)) {
    return new Date(`${f.value}T00:00:00`).toLocaleDateString("en-KE", { day: "numeric", month: "short", year: "numeric" });
  }
  if (f.name === "pay_period" && /^\d{4}-\d{2}$/.test(f.value)) {
    return new Date(`${f.value}-01T00:00:00`).toLocaleDateString("en-KE", { month: "long", year: "numeric" });
  }
  return f.value;
}

export function certainty(f: DocField): { label: string; tone: string } {
  if (f.value == null || f.value === "") return { label: "Not found", tone: "certainty--none" };
  if (f.problems.includes("corrected by a reviewer")) return { label: "Corrected", tone: "certainty--clear" };
  if (f.confidence >= 0.85) return { label: "Clear", tone: "certainty--clear" };
  if (f.confidence >= 0.5) return { label: "Unsure", tone: "certainty--unsure" };
  return { label: "Doubtful", tone: "certainty--doubtful" };
}

export function DocFields({ fields }: { fields: DocField[] }) {
  return (
    <dl className="doc-fields">
      {fields.map((f) => {
        const c = certainty(f);
        return (
          <div key={f.name} className="doc-field">
            <dt>{f.label}</dt>
            <dd>
              <span className="tabular">{fieldValue(f)}</span>
              <span className={`certainty ${c.tone}`} title={f.problems.join("; ") || undefined}>
                {c.label}
              </span>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function DocChecks({ checks }: { checks: DocCheck[] }) {
  return (
    <ul className="doc-checks">
      {checks.map((c) => (
        <li key={c.name} className={`doc-check doc-check--${c.ok === true ? "ok" : c.ok === false ? "bad" : "skip"}`}>
          <span className="doc-check-mark" aria-hidden="true">
            {c.ok === true ? "✓" : c.ok === false ? "✗" : "–"}
          </span>
          <span>
            <span className="visually-hidden">{c.ok === true ? "Passed: " : c.ok === false ? "Failed: " : "Not checked: "}</span>
            {c.label}
            <span className="muted small"> · {c.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
