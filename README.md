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
| 1 | Sources: job boards (JobSpy), company ATS boards, de-dup pipeline | ⏳ |
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
uv run internradar init      # creates data/internradar.db, validates config/search.yaml
uv run pytest
```

Edit `config/search.yaml` to change roles, cities and batch.

## Layout

```
backend/   FastAPI + SQLModel service and CLI (Python 3.11, uv)
config/    search.yaml: roles, locations, batch
data/      local SQLite database (git-ignored)
```

## License

MIT
