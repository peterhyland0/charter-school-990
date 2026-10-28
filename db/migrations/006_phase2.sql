-- Phase 2: roles among linked nonprofits, and hand corrections.

-- A hand fix to one school's match. The matcher rebuilds its tables on every
-- run and applies the active corrections last, so a correction always wins and
-- survives reruns. Deliberately no foreign key to schools: that table is
-- reloaded with TRUNCATE ... CASCADE on every run, which would wipe these.
CREATE TABLE IF NOT EXISTS match_corrections (
    id          bigserial PRIMARY KEY,
    ncessch     text NOT NULL,
    ein         text,
    action      text NOT NULL CHECK (action IN ('link', 'unlink', 'none')),
    note        text NOT NULL CHECK (btrim(note) <> ''),
    created_by  text NOT NULL DEFAULT current_user,
    created_at  timestamptz NOT NULL DEFAULT now(),
    retired_at  timestamptz,
    CHECK ((action = 'none') = (ein IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS match_corrections_active
    ON match_corrections (ncessch, coalesce(ein, '')) WHERE retired_at IS NULL;

-- What each linked nonprofit is to the school:
--   operator    runs the school
--   management  manages the operator's schools (the operator delegates to it)
--   support     a foundation, facilities or holding company in the network
--   affiliate   the same network in another state
--   related     tied with the operator but lost on name similarity
--   other       weaker evidence than the school's best match
ALTER TABLE school_matches ADD COLUMN IF NOT EXISTS role text;
ALTER TABLE school_matches ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'auto';
ALTER TABLE school_matches DROP CONSTRAINT IF EXISTS school_matches_role_check;
ALTER TABLE school_matches ADD CONSTRAINT school_matches_role_check
    CHECK (role IN ('operator', 'management', 'support', 'affiliate', 'related', 'other'));
ALTER TABLE school_matches DROP CONSTRAINT IF EXISTS school_matches_source_check;
ALTER TABLE school_matches ADD CONSTRAINT school_matches_source_check CHECK (source IN ('auto', 'corrected'));

ALTER TABLE school_status ADD COLUMN IF NOT EXISTS corrected boolean NOT NULL DEFAULT false;

DROP VIEW IF EXISTS school_profile;
CREATE VIEW school_profile AS
SELECT s.ncessch, s.sch_name, s.lea_name, s.lcity, s.st, s.website AS school_website,
       st.status, st.reason, st.corrected, m.ein, m.tier, m.role, m.source, m.signals, m.ambiguous, m.evidence,
       f.object_id, f.org_name, f.return_type, f.tax_period_begin, f.tax_period_end,
       f.total_revenue, f.mgmt_delegated, f.source_zip, f.amended
FROM schools s
LEFT JOIN school_status st ON st.ncessch = s.ncessch
LEFT JOIN school_matches m ON m.ncessch = s.ncessch
LEFT JOIN filings f ON f.object_id = m.object_id;
