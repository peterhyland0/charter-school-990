-- Phase 1: national coverage, more matching signals, tiers and reasons.
-- Additive and safe to apply repeatedly.

ALTER TABLE schools
    ADD COLUMN IF NOT EXISTS lea_type     text,
    ADD COLUMN IF NOT EXISTS zip5         text,
    ADD COLUMN IF NOT EXISTS phone10      text,
    ADD COLUMN IF NOT EXISTS name_key     text,
    ADD COLUMN IF NOT EXISTS lea_name_key text;

ALTER TABLE bmf_orgs
    ADD COLUMN IF NOT EXISTS street        text,
    ADD COLUMN IF NOT EXISTS zip5          text,
    ADD COLUMN IF NOT EXISTS name_key      text,
    ADD COLUMN IF NOT EXISTS sort_name_key text;

ALTER TABLE filings
    ADD COLUMN IF NOT EXISTS phone10  text,
    ADD COLUMN IF NOT EXISTS street   text,
    ADD COLUMN IF NOT EXISTS city     text,
    ADD COLUMN IF NOT EXISTS state    text,
    ADD COLUMN IF NOT EXISTS zip5     text,
    ADD COLUMN IF NOT EXISTS name_key text,
    ADD COLUMN IF NOT EXISTS dba_key  text;

-- Matches are rebuilt on every run, so old phase 0 tiers can simply go.
DELETE FROM school_matches WHERE tier NOT IN ('verified', 'likely');
ALTER TABLE school_matches DROP CONSTRAINT IF EXISTS school_matches_tier_check;
ALTER TABLE school_matches ADD CONSTRAINT school_matches_tier_check CHECK (tier IN ('verified', 'likely'));
ALTER TABLE school_matches
    ADD COLUMN IF NOT EXISTS signals   text[]  NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS ambiguous boolean NOT NULL DEFAULT false;

-- One row per operating school: its best match tier, or why it has none.
CREATE TABLE IF NOT EXISTS school_status (
    ncessch       text PRIMARY KEY REFERENCES schools (ncessch),
    status        text NOT NULL CHECK (status IN ('verified', 'likely', 'unmatched')),
    n_candidates  int  NOT NULL,
    reason        text,
    detail        jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS filings_name_trgm ON filings USING gin (name_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS filings_dba_trgm  ON filings USING gin (dba_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS filings_zip5      ON filings (zip5);
CREATE INDEX IF NOT EXISTS filings_phone10   ON filings (phone10);
CREATE INDEX IF NOT EXISTS schools_zip5      ON schools (zip5);
CREATE INDEX IF NOT EXISTS schools_name_trgm ON schools USING gin (sch_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS bmf_orgs_zip5     ON bmf_orgs (zip5);

-- Picks up the new filings columns.
CREATE OR REPLACE VIEW latest_filings AS
SELECT DISTINCT ON (ein) *
FROM filings
ORDER BY ein, tax_period_end DESC NULLS LAST, return_ts DESC NULLS LAST, object_id DESC;

DROP VIEW IF EXISTS school_profile;
CREATE VIEW school_profile AS
SELECT s.ncessch, s.sch_name, s.lea_name, s.lcity, s.st, s.website AS school_website,
       st.status, st.reason, m.ein, m.tier, m.signals, m.ambiguous, m.evidence,
       f.object_id, f.org_name, f.return_type, f.tax_period_begin, f.tax_period_end,
       f.total_revenue, f.mgmt_delegated, f.source_zip, f.amended
FROM schools s
LEFT JOIN school_status st ON st.ncessch = s.ncessch
LEFT JOIN school_matches m ON m.ncessch = s.ncessch
LEFT JOIN filings f ON f.object_id = m.object_id;
