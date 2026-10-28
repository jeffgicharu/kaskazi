import { DemoBanner, Wordmark } from "../components/Brand";

/** Shown if a page fails while drawing. The details go to the console, not to the visitor. */
export function ErrorPage() {
  return (
    <>
      <DemoBanner />
      <header className="site-header">
        <Wordmark />
      </header>
      <main id="main" className="not-found">
        <p className="eyebrow">Something went wrong</p>
        <h1>This page couldn't be shown.</h1>
        <p>Your sample copy is safe. Reload the page, or start again from the home page.</p>
        <div className="slip-buttons">
          <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
            Reload
          </button>
          <a className="btn btn-quiet" href="/">
            Home page
          </a>
        </div>
      </main>
    </>
  );
}
