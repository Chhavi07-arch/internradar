# Phase A: multi-user foundation

InternRadar has two entry points. `uv run internradar serve` is the original loopback-only SQLite application and still supports the desktop Gmail connector. Do not expose it on the public internet. The PostgreSQL entry point is `uv run uvicorn internradar.hosted:create_hosted_app --factory`; put it behind HTTPS and serve the built Vite frontend from the same origin. No live service is configured by this change.

For the PostgreSQL entry point set `INTERNRADAR_WEB_DATABASE_URL`, `INTERNRADAR_WEB_ORIGIN` (exact HTTPS origin, no trailing slash), `INTERNRADAR_SIGNIN_CLIENT_ID`, `INTERNRADAR_SIGNIN_CLIENT_SECRET`, and `INTERNRADAR_SIGNIN_STATE_SECRET` (random, at least 32 characters). Register `<WEB_ORIGIN>/auth/callback` on a separate Google **web** OAuth client for sign-in only (`openid email profile`). Google issuer and subject identify the account; the app issues a separate opaque, hashed-at-rest session. Login neither needs nor grants mailbox access. A real Google sign-in was verified locally, including the callback and creation of an authenticated session. This did not grant Gmail access.

For local Google sign-in, Google allows a loopback HTTP callback. Set
`INTERNRADAR_LOCAL_HTTP=1` and `INTERNRADAR_WEB_ORIGIN=http://localhost:8448`, and register
`http://localhost:8448/auth/callback` on the **dev web client**. Bind Uvicorn to `127.0.0.1`
with `--port 8448 --no-proxy-headers --no-access-log`. This mode rejects non-loopback peers,
unexpected Host headers, and forwarded requests. It uses separate development cookies and
requires no Keychain changes. Never use it behind a proxy or for a public deployment; leave
the flag unset and use HTTPS in hosted mode. Keep secrets in local ignored settings.

The hosted dashboard reads the scheduled catalogue; its Refresh button reloads results rather than launching network fetches for each student. The profile API has an independent read/write record per account. The dashboard design is unchanged.

## Reproduce the database checks

Create a disposable UTF-8 PostgreSQL database. From `backend/`, set `INTERNRADAR_MIGRATION_DATABASE_URL` to a `postgresql+psycopg://` URL for the migration owner and run `uv run alembic upgrade head`. Do not point this at a database with the old SQLite tables imported into it.

For integration tests only, create a separate login that does not own the tables, cannot assume the migration role, and has neither superuser nor `BYPASSRLS`. Grant the following in the disposable database (replace `ir_test_runtime` with that login):

```sql
GRANT SELECT ON alembic_version TO ir_test_runtime;
GRANT SELECT, INSERT, DELETE ON account TO ir_test_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    mail_connection, mail_delivery, private_opportunity,
    opportunity, sighting, run, sourcerun, fundingevent, fundingmention
    TO ir_test_runtime;
GRANT SELECT, INSERT, UPDATE ON user_opportunity TO ir_test_runtime;
GRANT SELECT, INSERT, DELETE ON web_session TO ir_test_runtime;
GRANT SELECT, INSERT, UPDATE ON student_profile, public_worker_job, private_worker_job TO ir_test_runtime;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO ir_test_runtime;
```

These fixture grants are intentionally broader than a production service needs. In production, separate catalogue-write, account management and private ingestion privileges. Only trusted server code may set the transaction's owner context; database clients must not be exposed to students.

Set `INTERNRADAR_TEST_RUNTIME_URL` to the test runtime login's URL and run `uv run pytest -q`. The ownership tests skip if the variable is absent. `uv run internradar audit-legacy /path/to/backup.db` reports counts without importing or displaying mail or notes. A listing with public and private sightings is never a public import candidate; rebuild its public side from the original public source.

To run import-specific tests, also set `INTERNRADAR_TEST_LEGACY_ADMIN_URL` and `INTERNRADAR_TEST_LEGACY_RUNTIME_URL` for a separate disposable migrated database named `ir_legacy_import_test`. Use the migration owner for setup and a non-owner login with SELECT on `opportunity`, `sighting` and `account`, SELECT/INSERT on `mail_connection`, `mail_delivery`, `private_opportunity` and `user_opportunity`, and sequence usage. Do not use a populated database for either test target.

## Workers and rehearsal

The public worker runs with `INTERNRADAR_CATALOGUE_DATABASE_URL` and `uv run python -m internradar.catalogue_worker --schedule` (`--once` performs one scheduler tick). ATS runs every 30 minutes; job boards and funding every six hours. Manual `--only ats` and `--funding` remain available. Private maintenance uses `run_private_jobs` with a trusted owner ID; it does not enumerate other accounts or reach real providers unless a revocation callback is supplied.

Rebuild the public catalogue from public sources first. Set `INTERNRADAR_LEGACY_IMPORT_DATABASE_URL` explicitly and run `uv run python -m internradar.legacy_import /path/to/backup.sqlite --owner <existing UUID>`. This is a read-only preview unless `--apply` is supplied. Use a standalone copy produced by SQLite backup, without WAL sidecars. The importer never inserts into the public catalogue. It remaps state to verified public sightings when possible and otherwise archives it privately. It does not copy raw bodies, pending mail, headers, or tokens. In the local rehearsal, an empty destination catalogue caused four state records to be archived privately; 43 processed deliveries and 48 extracted postings were imported. Re-running created no new rows. Do not cut over the live database without separate approval.

## Gates before real students or mail

- Configure a separate production Google sign-in client and verify the deployed HTTPS flow. Do not reuse the desktop Gmail client. Public signup remains independent of Gmail.
- Direct Gmail OAuth and provider-backed forwarding are not enabled. Both connect to the same normalized, owner-scoped pipeline once configured. Confirm OAuth, forwarding provider security and retention, and AI data-handling requirements before processing real student mail.
- The account deletion and revocation services were exercised with synthetic data and a fake provider; they are not exposed as a production self-service flow. Hosted private maintenance has no automatic owner dispatcher. These must be wired and tested before opening real inbox access.

The local connector clears successful, irrelevant, and expired raw bodies at sync time. PostgreSQL encrypts staged payloads and clears them on successful extraction; its private purge job removes expired payloads for the specified owner. Deleting a database field does not erase old backups, SQLite WAL files, or provider copies.
