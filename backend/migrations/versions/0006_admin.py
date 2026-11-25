"""Fixed aggregate metrics and operator-only role-change audit."""

from alembic import op

revision = "0006_admin"
down_revision = "0005_profile"


def upgrade() -> None:
    # Roles are cluster-wide. Reuse the dedicated names across isolated databases;
    # never alter an existing role (credentials and membership are operator-managed).
    op.execute("""
        DO $$
        DECLARE name text;
        BEGIN
            FOREACH name IN ARRAY ARRAY['internradar_metrics_owner', 'internradar_metrics_reader']
            LOOP
                IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = name) THEN
                    EXECUTE pg_catalog.format(
                        'CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS '
                        'NOCREATEDB NOCREATEROLE NOREPLICATION', name);
                END IF;
            END LOOP;
            IF EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles r
                WHERE r.rolname IN ('internradar_metrics_owner', 'internradar_metrics_reader')
                AND (r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole
                    OR r.rolreplication OR r.rolinherit
                    OR (r.rolname = 'internradar_metrics_owner' AND r.rolcanlogin)
                    OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member = r.oid)
                    OR (r.rolname = 'internradar_metrics_reader' AND EXISTS (
                        SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.roleid = r.oid
                    )))
            ) THEN
                RAISE EXCEPTION 'Unsafe metrics role configuration';
            END IF;
        END $$
    """)
    op.execute("CREATE SCHEMA internradar_admin")
    op.execute("REVOKE ALL ON SCHEMA internradar_admin FROM PUBLIC")
    op.execute("""
        CREATE TABLE internradar_admin.role_audit (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            operator_role name NOT NULL DEFAULT session_user,
            target_account_id uuid NOT NULL,
            previous_role text NOT NULL CHECK (previous_role IN ('admin', 'customer')),
            new_role text NOT NULL CHECK (new_role IN ('admin', 'customer')),
            CHECK (previous_role <> new_role)
        )
    """)
    op.execute("REVOKE ALL ON TABLE internradar_admin.role_audit FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA internradar_admin TO internradar_metrics_owner")
    op.execute("GRANT USAGE ON SCHEMA public TO internradar_metrics_owner")
    op.execute("GRANT SELECT (created_at) ON public.account TO internradar_metrics_owner")
    op.execute("""
        CREATE POLICY aggregate_accounts ON public.account FOR SELECT
        TO internradar_metrics_owner USING (true)
    """)
    op.execute("GRANT SELECT (id) ON public.opportunity TO internradar_metrics_owner")
    op.execute("GRANT SELECT (published_at) ON public.fundingevent TO internradar_metrics_owner")
    op.execute(
        "GRANT SELECT (id, started_at, finished_at) ON public.run TO internradar_metrics_owner"
    )
    op.execute("""
        GRANT SELECT (id, run_id, source, ok) ON public.sourcerun TO internradar_metrics_owner
    """)
    op.execute("""
        CREATE FUNCTION internradar_admin.metrics() RETURNS jsonb
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $function$
            WITH attempts AS (
                SELECT s.source, s.ok, r.started_at, r.finished_at,
                       row_number() OVER (PARTITION BY s.source
                           ORDER BY r.started_at DESC, r.id DESC, s.id DESC) AS recency,
                       max(r.finished_at) FILTER (WHERE s.ok)
                           OVER (PARTITION BY s.source) AS last_success
                FROM public.sourcerun s JOIN public.run r ON r.id = s.run_id
            )
            SELECT jsonb_build_object(
                'current_accounts', (SELECT count(1) FROM public.account),
                'catalogue_listings', (SELECT count(id) FROM public.opportunity),
                'funding_events_last_30_days', (
                    SELECT count(published_at) FROM public.fundingevent
                    WHERE published_at >= statement_timestamp() - interval '720 hours'
                      AND published_at <= statement_timestamp()
                ),
                'sources', coalesce((
                    SELECT jsonb_agg(jsonb_build_object(
                        'source', source,
                        'latest_attempt_at', started_at,
                        'latest_attempt_ok', CASE WHEN finished_at IS NOT NULL THEN ok END,
                        'last_successful_refresh_at', last_success
                    ) ORDER BY source)
                    FROM attempts WHERE recency = 1
                ), '[]'::jsonb)
            )
        $function$
    """)
    op.execute("REVOKE ALL ON FUNCTION internradar_admin.metrics() FROM PUBLIC")
    op.execute("ALTER FUNCTION internradar_admin.metrics() OWNER TO internradar_metrics_owner")
    op.execute("GRANT USAGE ON SCHEMA internradar_admin TO internradar_metrics_reader")
    op.execute(
        "GRANT EXECUTE ON FUNCTION internradar_admin.metrics() TO internradar_metrics_reader"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION internradar_admin.metrics()")
    op.execute("DROP TABLE internradar_admin.role_audit")
    op.execute("DROP SCHEMA internradar_admin")
    op.execute("DROP POLICY aggregate_accounts ON public.account")
    op.execute("REVOKE SELECT (created_at) ON public.account FROM internradar_metrics_owner")
    op.execute("REVOKE SELECT (id) ON public.opportunity FROM internradar_metrics_owner")
    op.execute("REVOKE SELECT (published_at) ON public.fundingevent FROM internradar_metrics_owner")
    op.execute("""
        REVOKE SELECT (id, started_at, finished_at) ON public.run FROM internradar_metrics_owner
    """)
    op.execute(
        "REVOKE SELECT (id, run_id, source, ok) ON public.sourcerun FROM internradar_metrics_owner"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM internradar_metrics_owner")
    # The cluster-wide roles may still be used by another database.
