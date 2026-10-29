import { Link } from "react-router";

export function Mark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <rect width="64" height="64" rx="16" fill="var(--ink)" />
      <path d="M22 12v40" stroke="var(--paper)" strokeWidth="4" strokeLinecap="round" />
      <path d="M26 14c13 6 19 15 18 30-6-3-12-4-18-3z" fill="var(--saffron)" />
      <path d="M12 50c10-4 30-4 40 0" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" fill="none" />
    </svg>
  );
}

export function Wordmark({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="wordmark" aria-label="Kaskazi Bank, home">
      <Mark size={30} />
      <span className="wordmark-text">Kaskazi</span>
    </Link>
  );
}
