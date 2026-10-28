import { Link } from "react-router";

import { DemoBanner, Wordmark } from "../components/Brand";

export function NotFound() {
  return (
    <>
      <DemoBanner />
      <header className="site-header">
        <Wordmark />
      </header>
      <main id="main" className="not-found">
        <p className="eyebrow">Not found</p>
        <h1>There's no page here.</h1>
        <p>It may have moved, or the link may be mistyped.</p>
        <Link className="btn btn-primary" to="/">Go to the home page</Link>
      </main>
    </>
  );
}
