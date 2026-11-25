ALTER TABLE catalogue.sources
    ADD COLUMN board_slug text CHECK (board_slug ~ '^[a-z0-9][a-z0-9_-]{0,127}$'),
    ADD COLUMN company_id uuid REFERENCES catalogue.companies(id),
    ADD COLUMN last_successful_window timestamptz;

ALTER TABLE catalogue.sightings
    ADD COLUMN consecutive_misses integer NOT NULL DEFAULT 0 CHECK (consecutive_misses >= 0);

ALTER TABLE schedule.tasks
    ADD COLUMN source_id uuid REFERENCES catalogue.sources(id),
    ADD COLUMN window_start timestamptz,
    ADD COLUMN state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'running', 'succeeded', 'failed')),
    ADD COLUMN attempt integer NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 4),
    ADD COLUMN claim_token uuid,
    ADD COLUMN error_code text,
    ADD UNIQUE (source_id, window_start);

ALTER TABLE schedule.outbox ADD COLUMN message_key text;

INSERT INTO catalogue.companies (slug, name, website_url)
VALUES ('stripe', 'Stripe', 'https://stripe.com');

INSERT INTO catalogue.sources (source_key, kind, endpoint_url, board_slug, company_id)
SELECT 'greenhouse:stripe', 'greenhouse', 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs', 'stripe', id
FROM catalogue.companies WHERE slug = 'stripe';
