# InternRadar v2 architecture

This is the canonical architecture for the greenfield build in `v2/`. The [Phase A checkpoint](phase-a.md) and [former proposal](archive/architecture-2026-11-24.md) describe the separate legacy app. No data migration or production cutover is implied.

## Service boundaries

| Component | Responsibility | Deployment |
|---|---|---|
| Frontend | Onboarding, recommendations, saved jobs and outreach UI. Calls one same-origin API. | React + TypeScript + Vite, served by Spring Boot |
| Core | Google OIDC, sessions, profiles, saves and API authorization. | Spring Boot web app |
| Recommendation | Select and rank shared jobs against each student's interests; explain matches. | Web module |
| Research | Scheduled public source discovery, job normalization and deduplication, company and funding evidence. | Separate Spring Boot worker |
| Applying | Student-reviewed application and outreach drafts. No automatic submission or sending. | Future web module |
| Email | Optional inbox processing and, later, explicitly approved sends. Private leads never become public listings. | Future isolated worker |

## Data and work flow

```text
Browser ── HTTPS / Coder proxy ──► Web (React + Spring Boot)
                                      │ synchronous reads and writes
                                      ▼
                                  PostgreSQL ◄── Research writes public results
                               shared / private / jobs         ▲
                                      │ outbox                 │
                                      ▼                        │
                                   Kafka ─────────────────────┘
                               background dispatch           Public ATS / funding
```

PostgreSQL is the source of truth; Kafka transports asynchronous work. Signing in, editing interests, ranking a shortlist and saving jobs do not trigger a source search. The research worker creates one PostgreSQL task and outbox entry per source/window, publishes to Kafka before marking the outbox row delivered, then acknowledges consumption only after a durable result or retry. Retries are bounded; terminal failures go to a dead-letter topic. Duplicate delivery is expected and must not create duplicate listings. Funding announcements are evidence to investigate, not proof of hiring or a trigger to send email.

## Current implementation

- Scaffold: Maven reactor (Java 25, Spring Boot), React shell, PostgreSQL/Flyway schema and role bootstrap, and Kafka topic registration. The React shell deliberately does not show fictional feeds or offer an authentication bypass.
- Research import code: a configured Stripe Greenhouse board, half-hour tasks, a PostgreSQL outbox, and a Kafka consumer with task leases, bounded source retries, a dead-letter topic, and idempotent listing writes. Local Compose verification passed: the worker imported the live Stripe board through Kafka into PostgreSQL, then committed a replay of the completed task without changing listings or sightings. Worker interruption, retry exhaustion, and dead-letter handling still need dedicated tests.

## Build phases and acceptance gates

| Phase | Remaining work | Done when |
|---|---|---|
| 1. Research and Kafka | Java 25 tests and live import/replay passed. Still verify worker interruption, retry exhaustion, and dead-letter handling. | A public import reaches PostgreSQL; replay does not change the catalogue; failed fetches do not close jobs; interrupted tasks recover or reach the dead-letter topic. |
| 2. Student experience | Google sign-in, onboarding, explainable recommendations, and private saves. Add source/domain coverage beyond the first board. | Two accounts rank the same catalogue differently and cannot read or change one another's saved jobs. Opening a feed does not enqueue research. |
| 3. Funding and outreach | Collect funding evidence, verify careers pages, recommend relevant companies, and build reviewed outreach drafts. | A verified round surfaces as a company lead, not proof of hiring. Students can edit drafts and send them from their own email client. |
| 4. Inbox and applications | Add opt-in private mailbox processing, application tracking, and disconnect/deletion flows. | Placement leads and updates are visible only to their owner. Uncertain matches require review. No automatic submission or outbound mail. |
| 5. Release verification | Browser journeys, account isolation, queue and API load, restart recovery, and backup restore. | End-to-end checks pass without depending on Coder. Only then select and verify the Coder deployment separately. |

## Local startup and Coder

For backend verification outside Docker, point `JAVA_HOME` at a Java 25 JDK and put `$JAVA_HOME/bin` first on `PATH`. Confirm `java -version`, then run `./mvnw -B verify` from `v2/backend/`. The Java 25 verification passed locally (29 Greenhouse adapter tests); this does not replace the live Kafka replay check below.

From `v2/`, copy `.env.example` to `.env`, set a distinct random password for each role, then run `docker compose up --build -d`. Open `http://127.0.0.1:8080/` (or the configured `WEB_PORT`). Check `docker compose ps -a` and `/actuator/health`. The one-shot Flyway container should exit successfully; the PostgreSQL role bootstrap runs only on a fresh volume. Changing `.env` after initialization does not rotate database passwords. Kafka and PostgreSQL ports are not published. The pinned Flyway image runs as AMD64 and requires emulation on ARM hosts.

For the Phase 1 import/replay check, use a fresh disposable Compose project: from `v2/`, run `docker compose -p internradar-v2-smoke up --build -d` (set a free `WEB_PORT` in `.env` if needed), then `bash scripts/smoke-research.sh --local-smoke`. The check waits for a real Stripe import, republishes its completed Kafka task, waits for the consumer group to commit the replay, and checks that its source sightings and listings are unchanged. Run away from the half-hour scheduling boundary to avoid a new poll during the comparison. When done, stop the disposable project with `docker compose -p internradar-v2-smoke down`.

For Coder, use the same Compose stack only after confirming the workspace and Docker access. Route the Coder app to the web port from the workspace, keep the broker and DB private, and verify HTTPS and OAuth callback registration before enabling real login. Named volumes provide persistence on one workspace host, not high availability; verify the volume backing and test backups outside the workspace before a pilot.
