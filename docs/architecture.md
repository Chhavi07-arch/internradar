# InternRadar v2: a multi-user, self-updating career system

> **Current direction:** Public signup does not depend on Gmail. Direct Gmail OAuth is the
> long-term primary integration and forwarding is the launch alternative and ongoing backup.
> Both use the same private pipeline. Next.js is an option, not a commitment; the current UI
> stays for Phase A. See [the Phase A checkpoint](phase-a.md) for actual implementation status.

Status: **proposal for review** (2026-11-24). Nothing here is built yet.

## Goal

Move from a single-user tool to a product other students can sign up for, built around
one idea: **a personal profile that is the single source of truth**, kept up to date by
connectors, so the system works without the user until something genuinely needs them.

Three changes, planned together because they all touch the data model:

1. **Profile bucket**: one structured, versioned record of who the user is.
2. **Multi-user**: Google sign-in, an *admin* role (me) and *customer* role (everyone else).
3. **Next.js dashboard**: rebuilt and redesigned on top of the new model.

## What stays the same

The fetch engine is the asset and does not change: sources → relevance → tiered
de-duplication → lifecycle, the funding radar, email extraction with in-code validation.
FastAPI stays as the backend; all of this is Python and well tested (150 tests).

## Architecture

```
            ┌──────────────── Next.js (Vercel) ────────────────┐
 browser ──▶│ landing · onboarding · app · admin console       │
            │ Auth.js: Google sign-in (openid, email, profile) │
            └───────────────┬──────────────────────────────────┘
                            │ server-side calls, signed user token
            ┌───────────────▼──────────────┐      ┌───────────────────────┐
            │ FastAPI (API)                │◀────▶│ Postgres              │
            └───────────────┬──────────────┘      │ shared catalog        │
            ┌───────────────▼──────────────┐      │ + per-user data       │
            │ Worker (scheduler)           │─────▶│                       │
            │ global fetch · funding radar │      └───────────────────────┘
            │ per-user Gmail sync          │
            └──────────────────────────────┘
```

- **Postgres replaces SQLite** (many users writing at once, hosting). Schema changes move
  to **Alembic** migrations, replacing today's add-missing-columns helper.
- **A worker process** runs everything on a schedule: company boards every 30 min, job
  boards every 6 h, funding radar every 6 h, each connected inbox every 30 min. Nobody has
  to press Refresh. A Postgres advisory lock replaces today's in-process fetch lock.
- **Auth lives in Next.js** (Auth.js, Google provider). Next.js calls FastAPI server-side
  with a short-lived token signed with a shared secret, carrying `user_id` and `role`.
  FastAPI trusts nothing else.

## Data model: shared catalog vs. per-user data

Fetching LinkedIn once per user would multiply rate-limit blocks, so the public catalog is
fetched **once for everyone** and personalised at read time.

| Shared (fetched once) | Per user |
|---|---|
| `Opportunity`, `Sighting` (public sources) | `User` (email, name, role, created_at, last_active_at) |
| `Run`, `SourceRun` | `Profile` (the bucket) and its version history |
| `FundingEvent`, `FundingMention` | `UserOpportunity`: seen, saved, hidden, notes, later application status |
| watchlist (admin-managed) | `GmailConnection` (encrypted tokens), `EmailMessage` |
| | private `Sighting`s from that user's inbox |

**Private email data.** A placement-cell email is the user's own. Sightings get an
`owner_user_id`: an opportunity is visible to a user if it has a public sighting *or* one
they own. A college email can still merge into the public LinkedIn card, but other users
never see that the email exists.

**User state moves off `Opportunity`** (today `seen_at`, `bookmarked`, `hidden`, `notes`
live on the shared row) into `UserOpportunity`.

## The profile bucket

One document per user, validated by a Pydantic schema, versioned on every change:

```yaml
identity:    {name, email, college, branch, batch, cgpa, backlogs}
preferences: {roles, cities, remote, min_stipend, start_date, dealbreakers}
skills:      [{name, level, evidence: [project ids, repo urls]}]
experience:  [{id, org, title, dates, bullets: [{id, text, tech, metrics}]}]
projects:    [{id, name, repo, tech, bullets: [...], stars, last_commit}]
links:       {github, linkedin, portfolio, leetcode}
answers:     [{question_key, answer}]   # reused for application forms
```

Every field records **provenance**: which connector set it and when, so the UI can show
"from GitHub, 2 days ago" next to each fact.

**Connectors propose, they never silently overwrite.**

| Connector | What it does | How |
|---|---|---|
| Resume import | Builds the first profile from the user's LaTeX or PDF resume | LLM extraction into the schema, user confirms once |
| GitHub | New repos, languages, stars, recent activity → new projects and skills | Public REST API by username |
| LinkedIn | Experience, education, skills | User uploads LinkedIn's official data export (ZIP). No scraping: LinkedIn has no profile-read API for individuals, and scraping violates its terms |
| Gmail | Offers, assessments, rejections → application status; placement mail → opportunities | Existing extraction pipeline |

Changes are tiered by risk:

- **Applied automatically:** factual, low-risk updates such as star counts, last commit
  date, a new repo's languages.
- **One-click approval:** anything that would change what appears on a resume or in an
  application, such as a new project, a new skill, a changed CGPA.

This is what "no intervention until it really matters" means in practice: the profile
stays current on its own, and the user only sees a short queue of real decisions.

**Everything downstream reads the bucket:** relevance and eligibility (batch, CGPA,
branch), the fit score, resume tailoring (bullet IDs, tech, metrics) and form answers.
`config/search.yaml` becomes the admin's default for new users.

## Users, roles and Google's rules

Google treats `gmail.readonly` as a **restricted scope**: while the OAuth app is in
*Testing* it works for at most **100 test users**, and going public needs verification plus
a paid third-party security assessment. Sign-in itself only needs basic scopes, which do
not. So the two are split:

| Google Cloud project | Scopes | Mode | Who |
|---|---|---|---|
| `internradar-signin` (new) | openid, email, profile | **Published** | anyone can sign up |
| `internradar-515612` (existing) | gmail.readonly | **Testing** | up to 100 invited users |

- Every user can sign up and get the shared feed, the funding radar, the profile bucket and
  GitHub / LinkedIn / resume imports.
- **Gmail is invite-only**: a user clicks *Request inbox access*; the admin approves, which
  adds them to an allowlist (and as a Google test user). This also makes a natural waitlist.
- Gmail moves from the desktop flow to a **web OAuth flow**; refresh tokens are encrypted at
  rest (Fernet key from the environment). Testing-mode tokens still expire after 7 days, so
  the worker detects expiry and the app shows a one-click reconnect.

**Admin console** (role `admin`):

- Users: total, sign-ups per day, weekly active, inboxes connected, a retention chart.
- Inbox access requests and the 100-slot allowlist.
- Source health, run history, watchlist editor.
- LLM usage and cost per user, with a per-user daily cap.

## Privacy

- Email bodies are deleted after extraction; only the extracted fields and subject line stay.
- *Delete my account* removes every per-user row and revokes the Google token.
- A privacy policy page (Google requires one for published sign-in).
- Admin views show aggregates; the admin cannot read users' emails or notes.

## Metrics worth showing in an interview

All measurable from data the system already collects:

- **Users**: sign-ups, weekly active users, inboxes connected.
- **Coverage**: internships indexed per week, number of sources, share merged as duplicates.
- **Speed**: how many hours earlier a role appears on a company board than on LinkedIn
  (both first-seen times are already stored per sighting).
- **Extraction quality**: precision and recall of email extraction on a labelled set of
  ~100 real emails (planned for block 7, unchanged).
- **Hiring signal**: share of funded startups that open an internship within 60 days.

## Next.js dashboard

App Router, TypeScript, Tailwind, shadcn/ui, TanStack Query for data, Auth.js.

- **Public:** landing page with live stats, sign in.
- **Onboarding:** import resume → connect GitHub → set preferences → (request inbox).
- **App:** feed, saved, deadlines, funding radar, profile (with provenance and the approval
  queue), connections, settings.
- **Admin:** the console above.

The visual design is decided with the user before any page is built (see open questions).

## Hosting (free tiers)

Next.js on Vercel, FastAPI and the worker on a container host (Render, Railway or Fly.io),
Postgres on Neon. One risk: job boards block datacenter IPs more aggressively than a home
connection, so LinkedIn results may drop once hosted. Company boards (Greenhouse, Lever,
Ashby), the funding feeds and Gmail are unaffected.

## Phases

Each phase ships working software and keeps every existing test passing.

| Phase | Scope | Done when |
|---|---|---|
| **A. Multi-user data model** | Postgres + Alembic, `User`, `UserOpportunity`, owned sightings and emails, worker with schedules. Existing data migrates to user #1 (admin). | The current single-user app runs unchanged on the new model; isolation tests prove user B cannot see user A's email data |
| **B. Profile bucket** | Schema, versions, provenance, proposals queue; resume, GitHub and LinkedIn-export importers; relevance reads the profile | Profile builds from a resume plus GitHub; a new repo shows up as a proposal without user action |
| **C. Auth and accounts** | Sign-in project, Auth.js, signed tokens to FastAPI, roles, web Gmail flow with encrypted tokens, inbox access requests | Two real accounts with separate data; admin approves a Gmail request end to end |
| **D. Next.js app** | Design system, onboarding, app pages, admin console | Feature parity with today's dashboard, plus profile and admin |
| **E. Launch** | Deploy, privacy policy, account deletion, product analytics | Friends can sign up at a public URL; the admin console shows them |

Then the original blocks continue on the new foundation: **4** eligibility and fit score,
**5** resume tailoring from the bucket, **6** application tracking, **7** assisted forms,
interview prep and the evaluation set.

## Open questions for review

1. **Design**: what did you dislike about the current dashboard, and are there products
   whose look you like (e.g. Linear, Vercel, Raycast, Notion)?
2. **Hosting**: are free tiers fine for now? Some hosts ask for a card even on free plans.
3. **Gmail access**: invite-only up to 100 users, as described above?
4. **Email retention**: delete bodies after extraction (recommended), or keep them so
   extraction can be re-run with a better model later?
