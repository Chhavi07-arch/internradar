# InternRadar

One dashboard for every internship: college placement mail, personal job-alert mail, job boards
and company career pages, de-duplicated into a single feed, scored against your profile and
tracked from discovery to offer.

> Status: early development. Built block by block; see the roadmap below.

## Why

Opportunities arrive in too many places: the placement cell's inbox, LinkedIn and Naukri alerts,
company career pages. The same role shows up three times with three different titles. InternRadar
collects all of it, merges duplicates, and shows what is new, what you are eligible for, what is
due soon and where you have already applied.

## Design principles

- **One card per role.** Every source is a *sighting* of an opportunity; tiered de-duplication
  (native id → canonical URL → company/title/city fingerprint) merges them.
- **Fetches never overwrite your state.** Bookmarks, notes and application status belong to you.
- **No fabrication.** Resume tailoring may only select and rephrase facts from your own profile,
  checked by code, not just by prompt.
- **Human in the loop.** Nothing is ever submitted on your behalf.

## Roadmap

| Block | Scope | Status |
|---|---|---|
| 0 | Foundation: schema, normalisation, config, CLI | ✅ |
| 1 | Sources: job boards (JobSpy), company ATS boards, de-dup pipeline | ✅ |
| 1b | Funding radar: recently funded startups, hiring signals | ✅ |
| 2 | Gmail ingestion: college + personal inbox | ✅ |
| 3 | Dashboard (Next.js) + notifications | |
| 4 | Eligibility gates + fit score | |
| 5 | LaTeX resume tailoring with fabrication checks, git-versioned | |
| 6 | Application tracking from email, analytics | |
| 7 | Assisted form filling, interview prep, evaluation metrics | |

## Getting started

```bash
cd backend
uv sync
uv run internradar init          # create data/internradar.db, validate config
uv run internradar fetch         # poll every source once
uv run internradar fetch -o ats  # only company career boards (fast)
uv run internradar list --new    # what arrived in the latest run
uv run internradar sources       # health of each source
uv run internradar radar         # funding news → funded startups → their careers boards
uv run internradar gmail connect college   # one-time, opens the browser (read-only)
uv run internradar gmail status            # what has been read from each inbox
uv run pytest
```

- `config/search.yaml`: roles, cities, batch.
- `config/watchlist.yaml`: companies whose Greenhouse / Lever / Ashby boards are polled directly.

> Keep the project outside iCloud-synced folders (Desktop/Documents on macOS). iCloud hides
> files inside dot-directories such as `.venv`, which breaks Python imports, and syncing a
> live SQLite file risks corrupting it.

## Sources

| Source | How | Exhaustive |
|---|---|---|
| LinkedIn, Indeed, Naukri | [JobSpy](https://github.com/speedyapply/JobSpy), small recent windows per role | no, expire by age |
| Company boards | public Greenhouse / Lever / Ashby JSON APIs | yes, closed after 2 missed runs |

## Gmail (college + personal inbox)

`fetch` also reads your connected inboxes. Cheap rules triage each email (job alert,
placement mail, application update, other); only the first two go to an LLM (OpenAI or Claude), which fills a
fixed schema: company, role, location, apply link, deadline, stipend and eligibility (CGPA,
branches, batches, backlogs).

Emails are **untrusted input**, so the model has no tools and its output is checked in code:
an apply link that does not literally appear in the email is dropped, and deadlines must be
real dates. Every email is downloaded and extracted once; failures are retried next sync.
Opportunities from email go through the same de-duplication as everything else, so a
LinkedIn alert and the LinkedIn listing are one card, and a passed deadline closes the card.

### One-time setup (about 5 minutes)

1. [Google Cloud console](https://console.cloud.google.com/) → create a project.
2. *APIs & Services → Library* → enable **Gmail API**.
3. *OAuth consent screen* → User type **External** → fill in the app name and your email →
   *Test users*: add every Gmail address you will connect.
4. *Credentials → Create credentials → OAuth client ID* → type **Desktop app** →
   download the JSON and save it as `data/google_client_secret.json`.
5. `cp .env.example .env` and set `OPENAI_API_KEY` (plus `OPENAI_BASE_URL` for a gateway) or `ANTHROPIC_API_KEY`.
6. `uv run internradar gmail connect college` (and `personal`).

The only scope requested is `gmail.readonly`. Tokens live in `data/tokens/` (git-ignored).
While the OAuth app is in *Testing*, Google expires tokens after 7 days; reconnect when
`fetch` says so. If a college Workspace account blocks third-party apps, set up a Gmail
filter that forwards placement mail to your personal inbox and connect that instead.

## Funding radar

A startup that has just raised money usually starts hiring within weeks, before its roles
reach the job boards. `internradar radar`:

1. reads funding news from Entrackr, Inc42, YourStory and Indian Startup News;
2. parses each headline into company, amount, round and lead investor with plain rules
   (no LLM), rejecting fund closes, stake sales, IPOs and "to raise" rumours;
3. merges reports of the same round across outlets;
4. looks for the company's Greenhouse / Lever / Ashby board, accepting it only if the page
   names the company **and** lists a role in India (a same-named board elsewhere is common);
5. hands every verified board to `fetch`, so a funded startup's internships surface the hour
   they are posted.

Every posting passes a relevance filter (whole-word "intern", tech role, Bengaluru or
India-open remote) before it is stored.

## Layout

```
backend/   SQLModel service and CLI (Python 3.11, uv)
  sources/   one module per source family, all implementing Source.fetch()
  pipeline   fetch → filter → de-duplicate → store → expire
config/    search.yaml, watchlist.yaml
data/      local SQLite database (git-ignored)
```

## License

MIT
