"""Admin metrics are separate from the owner-scoped student workspace."""

import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from internradar.web_auth import WebSettings, principal_for


class SourceHealth(BaseModel):
    source: str
    latest_attempt_at: datetime
    latest_attempt_ok: bool | None
    last_successful_refresh_at: datetime | None


class AdminMetrics(BaseModel):
    current_accounts: int
    catalogue_listings: int
    funding_events_last_30_days: int
    sources: list[SourceHealth]


def verify_web_account_acl(conn: Connection) -> None:
    """Do not run with credentials that can write role or change bound identities."""
    if (
        conn.scalar(
            text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles r
            WHERE pg_catalog.pg_has_role(r.oid, 'MEMBER')
              AND (has_column_privilege(r.oid, 'public.account', 'role', 'INSERT, UPDATE')
                   OR has_column_privilege(r.oid, 'public.account', 'identity_issuer', 'UPDATE')
                   OR has_column_privilege(r.oid, 'public.account', 'identity_subject', 'UPDATE')
                   OR has_column_privilege(r.oid, 'public.account', 'id', 'UPDATE')
                   OR has_schema_privilege(r.oid, 'internradar_admin', 'CREATE')
                   OR has_table_privilege(r.oid, (SELECT c.oid FROM pg_catalog.pg_class c
                       WHERE c.relnamespace = 'internradar_admin'::regnamespace
                         AND c.relname = 'role_audit'),
                       'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
                   OR has_any_column_privilege(r.oid, (SELECT c.oid FROM pg_catalog.pg_class c
                       WHERE c.relnamespace = 'internradar_admin'::regnamespace
                         AND c.relname = 'role_audit'),
                       'SELECT, INSERT, UPDATE')
                   OR has_function_privilege(r.oid, (SELECT p.oid FROM pg_catalog.pg_proc p
                       WHERE p.pronamespace = 'internradar_admin'::regnamespace
                         AND p.proname = 'metrics' AND p.pronargs = 0), 'EXECUTE'))
        )
    """)
        )
        is not False
    ):
        raise RuntimeError("The web login must not be able to change roles or access admin SQL")


# The metrics URL is not trusted to have been provisioned correctly: do not call
# the definer function from a web, operator, owner or otherwise privileged login.
_METRICS_PROVISIONED = text("""
    SELECT current_user = 'internradar_metrics_reader' AND current_user = session_user
       AND NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_roles r
           WHERE r.rolname IN ('internradar_metrics_reader', 'internradar_metrics_owner')
             AND (r.rolsuper OR r.rolbypassrls OR r.rolinherit OR r.rolcreaterole
                  OR r.rolcreatedb OR r.rolreplication
                  OR (r.rolname = 'internradar_metrics_owner' AND r.rolcanlogin)
                  OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member = r.oid)
                  OR (r.rolname = 'internradar_metrics_reader' AND EXISTS (
                      SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.roleid = r.oid
                  )))
       )
       AND NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_namespace n
           WHERE n.nspname IN ('public', 'internradar_admin')
             AND has_schema_privilege(n.oid, 'CREATE')
       )
       AND NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname IN ('public', 'internradar_admin') AND c.relkind IN ('r','p','v','m','f')
             AND (has_table_privilege(c.oid,
                      'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
                  OR has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'))
       )
       AND NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname IN ('public', 'internradar_admin') AND c.relkind = 'S'
             AND CASE WHEN c.relkind = 'S' THEN
                   has_sequence_privilege(c.oid, 'USAGE, SELECT, UPDATE') ELSE false END
       )
       AND EXISTS (
           SELECT 1 FROM pg_catalog.pg_class c
           JOIN pg_catalog.pg_policy pol ON pol.polrelid = c.oid
           WHERE c.oid = 'public.account'::regclass AND c.relrowsecurity AND c.relforcerowsecurity
             AND pol.polname = 'aggregate_accounts' AND pol.polcmd = 'r' AND pol.polpermissive
             AND pol.polroles = ARRAY['internradar_metrics_owner'::regrole::oid]
             AND pg_catalog.pg_get_expr(pol.polqual, pol.polrelid) = 'true'
       )
       AND EXISTS (
           SELECT 1 FROM pg_catalog.pg_proc p
           JOIN pg_catalog.pg_roles owner ON owner.oid = p.proowner
           WHERE p.oid = 'internradar_admin.metrics()'::regprocedure
             AND p.prosecdef AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']
             AND owner.rolname = 'internradar_metrics_owner'
             AND has_function_privilege(p.oid, 'EXECUTE')
             AND NOT EXISTS (
                 SELECT 1
                 FROM pg_catalog.aclexplode(coalesce(p.proacl, acldefault('f', p.proowner)))
                     grant_entry
                 WHERE grant_entry.privilege_type = 'EXECUTE'
                   AND (grant_entry.grantee NOT IN (p.proowner, current_user::regrole::oid)
                        OR (grant_entry.grantee <> p.proowner AND grant_entry.is_grantable))
             )
       )
""")


def _metrics_engine(web_engine: Engine) -> Engine | None:
    configured_url = os.environ.get("INTERNRADAR_METRICS_DATABASE_URL")
    if not configured_url:
        return None
    try:
        url = make_url(configured_url)
        if (
            url.drivername == "postgresql+psycopg"
            and url.username == "internradar_metrics_reader"
            and _database_address(url) == _database_address(web_engine.url)
        ):
            return create_engine(url, pool_pre_ping=True, hide_parameters=True)
    except (SQLAlchemyError, ValueError):
        pass  # Do not log connection strings or fall back to other credentials.
    return None


def _database_address(url: URL) -> tuple[object, ...]:
    # libpq accepts transport overrides in query parameters as well as the URL authority.
    return (
        url.database,
        url.query.get("dbname"),
        url.query.get("host", url.host),
        url.query.get("hostaddr"),
        url.query.get("port", str(url.port or 5432)),
        url.query.get("service"),
    )


def install_admin(app: FastAPI, engine: Engine, settings: WebSettings) -> None:
    metrics_engine = _metrics_engine(engine)

    @app.get("/api/admin/metrics", response_model=AdminMetrics)
    def metrics(request: Request, response: Response) -> AdminMetrics:
        principal = principal_for(engine, request.cookies.get(settings.session_cookie))
        if principal.role != "admin":
            raise HTTPException(403, "Admin access required", headers={"Cache-Control": "no-store"})
        unavailable = HTTPException(
            503, "Admin metrics unavailable", headers={"Cache-Control": "no-store"}
        )
        if metrics_engine is None:
            raise unavailable
        try:
            with metrics_engine.connect() as conn:
                # Resolve catalog names without trusting the login's default search path.
                conn.execute(text("SET LOCAL search_path = pg_catalog, pg_temp"))
                if conn.scalar(_METRICS_PROVISIONED) is not True:
                    raise unavailable
                result = AdminMetrics.model_validate(
                    conn.scalar(text("SELECT internradar_admin.metrics()"))
                )
        except (SQLAlchemyError, ValidationError):
            raise unavailable from None
        response.headers["Cache-Control"] = "no-store"
        return result
