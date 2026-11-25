CREATE SCHEMA app;
CREATE SCHEMA catalogue;
CREATE SCHEMA schedule;

CREATE TABLE app.accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer text NOT NULL CHECK (btrim(issuer) <> ''),
    subject text NOT NULL CHECK (btrim(subject) <> ''),
    email text NOT NULL CHECK (btrim(email) <> ''),
    display_name text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE app.profiles (
    account_id uuid PRIMARY KEY REFERENCES app.accounts(id) ON DELETE CASCADE,
    document jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(document) = 'object'),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalogue.companies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE CHECK (btrim(slug) <> ''),
    name text NOT NULL CHECK (btrim(name) <> ''),
    website_url text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalogue.sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key text NOT NULL UNIQUE CHECK (btrim(source_key) <> ''),
    kind text NOT NULL CHECK (btrim(kind) <> ''),
    endpoint_url text NOT NULL CHECK (btrim(endpoint_url) <> ''),
    enabled boolean NOT NULL DEFAULT true
);

CREATE TABLE catalogue.listings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid NOT NULL REFERENCES catalogue.companies(id),
    canonical_url text NOT NULL UNIQUE CHECK (btrim(canonical_url) <> ''),
    title text NOT NULL CHECK (btrim(title) <> ''),
    description text,
    location text,
    posted_at timestamptz,
    closes_at timestamptz,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    active boolean NOT NULL DEFAULT true,
    CHECK (last_seen_at >= first_seen_at)
);
CREATE INDEX listings_company_idx ON catalogue.listings (company_id);
CREATE INDEX listings_active_feed_idx ON catalogue.listings (first_seen_at DESC, id) WHERE active;

CREATE TABLE catalogue.sightings (
    source_id uuid NOT NULL REFERENCES catalogue.sources(id),
    external_id text NOT NULL CHECK (btrim(external_id) <> ''),
    listing_id uuid NOT NULL REFERENCES catalogue.listings(id),
    source_url text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, external_id),
    CHECK (last_seen_at >= first_seen_at)
);
CREATE INDEX sightings_listing_idx ON catalogue.sightings (listing_id);

CREATE TABLE app.saves (
    account_id uuid NOT NULL REFERENCES app.accounts(id) ON DELETE CASCADE,
    listing_id uuid NOT NULL REFERENCES catalogue.listings(id),
    saved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, listing_id)
);
CREATE INDEX saves_listing_idx ON app.saves (listing_id);

CREATE TABLE schedule.tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_key text NOT NULL UNIQUE CHECK (btrim(task_key) <> ''),
    kind text NOT NULL CHECK (btrim(kind) <> ''),
    payload jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
    next_run_at timestamptz NOT NULL DEFAULT now(),
    enabled boolean NOT NULL DEFAULT true,
    lease_until timestamptz
);
CREATE INDEX tasks_due_idx ON schedule.tasks (next_run_at, id) WHERE enabled;

CREATE TABLE schedule.outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic text NOT NULL CHECK (btrim(topic) <> ''),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
CREATE INDEX outbox_pending_idx ON schedule.outbox (available_at, id) WHERE published_at IS NULL;

-- Deny by default: no runtime DDL, private web data from research, or
-- catalogue mutation, source ingest state, and scheduling from the web.
REVOKE ALL ON SCHEMA app, catalogue, schedule FROM PUBLIC;
GRANT USAGE ON SCHEMA app, catalogue TO internradar_web;
GRANT USAGE ON SCHEMA catalogue, schedule TO internradar_research;

GRANT SELECT, INSERT, UPDATE, DELETE ON app.accounts, app.profiles, app.saves TO internradar_web;
GRANT SELECT ON catalogue.companies, catalogue.listings TO internradar_web;
GRANT SELECT, INSERT, UPDATE, DELETE ON catalogue.companies, catalogue.sources, catalogue.sightings,
    schedule.tasks, schedule.outbox TO internradar_research;
-- Retire listings in place: research cannot delete a saved listing or change its ID.
GRANT SELECT, INSERT ON catalogue.listings TO internradar_research;
GRANT UPDATE (company_id, canonical_url, title, description, location, posted_at, closes_at,
    first_seen_at, last_seen_at, active) ON catalogue.listings TO internradar_research;
