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
| 1b | Funding radar: recently funded startups, hiring signals | ⏳ |
| 2 | Gmail ingestion: college + personal inbox | |
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
