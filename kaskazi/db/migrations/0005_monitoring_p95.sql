-- Monitoring reports the 95th percentile of reply times, not the 90th. Migrations only add while
-- the release before is still running, so this is a new function beside app.site_turn_stats,
-- which a later release drops. Still numbers only: no text, conversation, copy or address.

CREATE FUNCTION app.visitor_turn_stats(p_since timestamptz)
RETURNS TABLE (
    day        date,
    outcome    text,
    turns      bigint,
    p50_ms     integer,
    p95_ms     integer,
    cost_usd   numeric,
    fallbacks  bigint
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT (t.at AT TIME ZONE 'Africa/Nairobi')::date,
           t.outcome,
           count(*),
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.latency_ms),
           percentile_disc(0.95) WITHIN GROUP (ORDER BY t.latency_ms),
           sum(t.cost_usd),
           count(*) FILTER (WHERE t.fallback)
    FROM app.turns t
    JOIN app.sandboxes s ON s.id = t.sandbox_id
    WHERE t.at >= p_since AND s.kind = 'visitor'
    GROUP BY 1, 2
    ORDER BY 1, 2
$$;
REVOKE ALL ON FUNCTION app.visitor_turn_stats(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.visitor_turn_stats(timestamptz) TO kaskazi_app;
