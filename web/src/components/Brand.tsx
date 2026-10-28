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

/** Always visible, on every page: nobody should mistake this for a real bank. */
export function DemoBanner() {
  return (
    <div className="demo-banner" role="note">
      <strong>Demonstration</strong>
      <span aria-hidden="true">·</span>
      <span>invented bank</span>
      <span aria-hidden="true">·</span>
      <span>sample data</span>
    </div>
  );
}

/** The kaskazi wind: a few drawn lines, the site's one flourish. */
export function WindLines({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 600 160" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path className="wind wind-1" d="M-10 120 C 120 60, 260 140, 400 80 S 560 40, 620 70" />
      <path className="wind wind-2" d="M-10 90 C 140 40, 280 110, 420 60 S 560 30, 620 40" />
      <path className="wind wind-3" d="M-10 140 C 100 110, 230 150, 380 120 S 540 90, 620 105" />
    </svg>
  );
}
