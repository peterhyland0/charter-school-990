-- State records saying who governs a charter school, used for honest
-- "district-run" reasons (assets/governance.py), and more external holder
-- sources: Florida's Auditor General audits and Sunbiz trade names
-- (assets/florida.py) and the schools' own websites (assets/websites.py).
CREATE TABLE IF NOT EXISTS school_governance (
    ncessch     text NOT NULL,
    source      text NOT NULL,
    -- district_run: run by its school district, no separate nonprofit;
    -- state_agency: a state body; independent: its own governing body.
    governance  text NOT NULL CHECK (governance IN ('district_run', 'state_agency', 'independent')),
    detail      jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (ncessch, source)
);

ALTER TABLE external_links DROP CONSTRAINT IF EXISTS external_links_source_check;
ALTER TABLE external_links ADD CONSTRAINT external_links_source_check
    CHECK (source IN ('fac', 'tx_tea', 'la_doe', 'az_ccd', 'fl_ag', 'fl_sunbiz', 'website'));

-- ADJUDICATOR=both applies picks the scored model and Jev agree on.
ALTER TABLE school_matches DROP CONSTRAINT IF EXISTS school_matches_source_check;
ALTER TABLE school_matches ADD CONSTRAINT school_matches_source_check
    CHECK (source IN ('auto', 'corrected', 'jev', 'model', 'consensus'));
