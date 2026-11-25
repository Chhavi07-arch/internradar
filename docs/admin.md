# Admin metrics and local enrollment

The hosted app retains the normal student workspace for admins. `GET /api/admin/metrics` uses the same browser session as the student routes and reads the current role on every request. It returns `401` when signed out, `403` for customers, and `503` for admins when the metrics connection is missing, unavailable, or unsafe. It never falls back to the web or migration credentials.

## Provision the database

For this milestone, use a local development database only. Apply Alembic through `0006_admin` using the database migration operator. `0006_admin` creates two cluster-wide role names if absent; it refuses to repurpose unsafe roles. The migration operator must have the rights to create roles and transfer function ownership (and reverse the migration if needed). Do not supply those credentials to the web process. Downgrade removes the database objects, not the cluster-wide roles; export the restricted audit table first if its history must be retained.

The database operator, not the web process, enables the executor login. On the target development cluster, use interactive `psql` and credentials from an ignored local secret store:

```sql
ALTER ROLE internradar_metrics_reader LOGIN;
\password internradar_metrics_reader
```

Allow that login to connect to the intended database only (via `pg_hba.conf` or equivalent managed PostgreSQL controls; add database `CONNECT` if it was revoked from `PUBLIC`). Keep it without memberships, object ownership or any other grants. It needs only schema `USAGE` and function `EXECUTE`, as granted by the migration. If your PostgreSQL cluster allows `PUBLIC` to create objects in `public`, revoke that schema privilege and grant schema creation back only to the intended database owner. Do not make `internradar_metrics_owner` a login or grant its membership to the web or executor logins.

Set `INTERNRADAR_METRICS_DATABASE_URL` for the hosted app to a separate `postgresql+psycopg://internradar_metrics_reader:…` URL for the same database. Set query logging/tracing to exclude credentials and parameters. Keep the URL unset when admin metrics are intentionally disabled.

The web login must **not** have table-wide `INSERT` on `account`, `UPDATE` on `role` or identity columns, membership in any privileged role, or permissions on the admin audit table or metrics function. Like all account-owned data, its normal account access remains RLS-scoped. Use grants such as the following, substituting the existing web login. An ordinary sign-in supplies only these three columns; the database supplies the `customer` default.

```sql
REVOKE INSERT, UPDATE ON public.account FROM ir_web;
GRANT SELECT ON public.account TO ir_web;
GRANT INSERT (id, identity_issuer, identity_subject) ON public.account TO ir_web;
```

Also remove any inherited or existing column-level role or identity write grants. These statements concern only `account`, not other existing web-session or student-profile grants.

For enrollment, provision a separate local operator login; do not grant these permissions to the web or metrics logins. Substitute that login for `ir_dev_operator`:

```sql
GRANT USAGE ON SCHEMA internradar_admin TO ir_dev_operator;
GRANT SELECT (id, identity_issuer, identity_subject, role), UPDATE (role)
    ON public.account TO ir_dev_operator;
GRANT SELECT (owner_user_id, expires_at) ON public.web_session TO ir_dev_operator;
GRANT INSERT (target_account_id, previous_role, new_role)
    ON internradar_admin.role_audit TO ir_dev_operator;
```

The operator uses transaction-scoped account RLS; `BYPASSRLS` is not needed. Use a named database login per operator so audit attribution is useful. Grant audit read access separately as needed.

## Verify and enroll each intended identity independently

1. In the local development app, complete a fresh Google **web** sign-in for the intended account. An operator must independently verify the Google account selected and associate that observed sign-in with its authenticated `/api/session` response. For example, witness the account chooser and ensuing authenticated session in the same browser; don't trust a reference or an email supplied in chat. Use a separate browser profile or sign out between accounts. If an account is blocked by the dev client's Google test-user configuration or Workspace policy, leave it pending. Desktop Gmail authorization does not count as web sign-in.
2. Read that session's own `account_ref` from `/api/session`; do not copy the session cookie. Treat the reference as private. Do not put references, emails, or Google subjects in command history, Git, tickets, screenshots, or logs.
3. From `backend/`, use a dedicated `INTERNRADAR_ADMIN_OPERATOR_DATABASE_URL` for the **local development** database. `uv run python -m internradar.admin_operator grant` prompts to confirm the database, reads the account reference without echo, and requires explicit verification. The command cannot match by email. It looks up the persisted Google `(issuer, subject)` by the deterministic account reference; the grant targets that exact pair and requires an unexpired web session. A Unix-socket URL can use `?host=/absolute/socket/directory&port=5432`. Do not use a tunnel or proxy to point this command at a production database.
4. Reload the app and check that the account has Admin navigation as well as its regular student workspace. Repeat the verification separately for the second account. Keep unverified accounts pending.

Use the same module with `revoke` and the independently established reference to demote an admin; revocation applies to existing sessions on the next request. Subsequent Google sign-ins do not re-grant access. No-op commands do not add audit entries. Actual transitions record time, the database operator's session role, the restricted internal target reference, and the old/new role atomically in `internradar_admin.role_audit`. Keep SQL statement/parameter logging and tracing off for enrollment, and access to this audit table limited to appropriately authorized operators.

## What the numbers mean

- **Current accounts** counts existing registered accounts, not historical registrations, active users, or retention. Deletion reduces the count.
- **Catalogue listings** includes all rows in the public catalogue, including closed listings, and excludes inbox or user-specific items.
- **Funding events in the last 30 days** counts public events with publication timestamps in the preceding 720 hours; it is not a spend estimate.
- **Source health** uses recorded public catalogue runs. Latest attempt is the latest recorded run start; latest result is unknown if that run has no finish time. Last successful refresh is the latest successful recorded completion and can be absent or older than a failed attempt. Sources with no recorded attempts do not appear. The funding event count is not a funding-worker health signal. No source error messages or private data are returned.
