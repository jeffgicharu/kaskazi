-- Site-wide monitoring for the staff console, in numbers only.
--
-- Each visitor's turns and ratings are visible only inside their own copy (row-level security).
-- The AI lead's monitoring view needs them summed across every visitor, so these two functions
-- run with the owner's rights and return aggregates: counts, timings, costs. No text, no
-- conversation, no copy, no address can come out of them. The release check's own copies are
-- left out, so the figures are visitors'.

CREATE FUNCTION app.site_turn_stats(p_since timestamptz)
RETURNS TABLE (
    day        date,
    outcome    text,
    turns      bigint,
    p50_ms     integer,
    p90_ms     integer,
    cost_usd   numeric,
    fallbacks  bigint
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT (t.at AT TIME ZONE 'Africa/Nairobi')::date,
           t.outcome,
           count(*),
           percentile_disc(0.5) WITHIN GROUP (ORDER BY t.latency_ms),
           percentile_disc(0.9) WITHIN GROUP (ORDER BY t.latency_ms),
           sum(t.cost_usd),
           count(*) FILTER (WHERE t.fallback)
    FROM app.turns t
    JOIN app.sandboxes s ON s.id = t.sandbox_id
    WHERE t.at >= p_since AND s.kind = 'visitor'
    GROUP BY 1, 2
    ORDER BY 1, 2
$$;
REVOKE ALL ON FUNCTION app.site_turn_stats(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.site_turn_stats(timestamptz) TO kaskazi_app;

CREATE FUNCTION app.site_feedback_stats(p_since timestamptz)
RETURNS TABLE (rating smallint, reason text, ratings bigint)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT f.rating, coalesce(f.reason, 'none'), count(*)
    FROM app.feedback f
    JOIN app.sandboxes s ON s.id = f.sandbox_id
    WHERE f.created_at >= p_since AND s.kind = 'visitor'
    GROUP BY 1, 2
    ORDER BY 1, 2
$$;
REVOKE ALL ON FUNCTION app.site_feedback_stats(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.site_feedback_stats(timestamptz) TO kaskazi_app;
