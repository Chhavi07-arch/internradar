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

PostgreSQL is the source of truth; Kafka transports asynchronous work. Signing in, editing interests, ranking a shortlist and saving jobs do not trigger a source search. The planned research path creates one PostgreSQL task and outbox entry per source/window, publishes to Kafka before marking the outbox row delivered, then acknowledges consumption only after a durable result or retry. Retries are bounded; terminal failures go to a dead-letter topic. Duplicate delivery is expected and must not create duplicate listings. Funding announcements are evidence to investigate, not proof of hiring or a trigger to send email.

## Current implementation

- Scaffold: Maven reactor (Java 25, Spring Boot), React shell, PostgreSQL/Flyway schema and role bootstrap, and Kafka topic registration. The React shell deliberately does not show fictional feeds or offer an authentication bypass.
- First product milestone: one real Greenhouse source through the Kafka task flow; two authenticated profiles see different recommendations from the same catalogue and can save listings independently. The scheduler, Kafka job consumer and publisher, Google sign-in, recommendation and saves APIs are not yet implemented.
- Later: funding discovery, draft-only outreach handed off to the student's email client, and opt-in email integration. No deployment target has been selected.

## Local startup and Coder

From `v2/`, copy `.env.example` to `.env`, set a distinct random password for each role, then run `docker compose up --build -d`. Open `http://127.0.0.1:8080/` (or the configured `WEB_PORT`). Check `docker compose ps -a` and `/actuator/health`. The one-shot Flyway container should exit successfully; the PostgreSQL role bootstrap runs only on a fresh volume. Changing `.env` after initialization does not rotate database passwords. Kafka and PostgreSQL ports are not published. The pinned Flyway image runs as AMD64 and requires emulation on ARM hosts.

For Coder, use the same Compose stack only after confirming the workspace and Docker access. Route the Coder app to the web port from the workspace, keep the broker and DB private, and verify HTTPS and OAuth callback registration before enabling real login. Named volumes provide persistence on one workspace host, not high availability; verify the volume backing and test backups outside the workspace before a pilot.
