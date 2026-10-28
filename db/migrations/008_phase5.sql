-- Phase 5: fuller finances, contractors, and the management explanation.

ALTER TABLE filings ADD COLUMN IF NOT EXISTS total_expenses bigint;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS total_assets bigint;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS total_liabilities bigint;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS net_assets bigint;
-- Schedule O text explaining Part VI line 3 (delegation of management duties).
ALTER TABLE filings ADD COLUMN IF NOT EXISTS mgmt_explanation text;

-- Part VII Section B: the five highest-paid independent contractors over $100k.
CREATE TABLE IF NOT EXISTS filing_contractors (
    object_id      text NOT NULL REFERENCES filings (object_id) ON DELETE CASCADE,
    seq            int  NOT NULL,
    name           text,
    services       text,
    compensation   bigint,
    -- The contractor runs schools for the filer: an education service
    -- provider or management company, or named in the delegation explanation.
    is_management  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (object_id, seq)
);
CREATE INDEX IF NOT EXISTS filing_contractors_management ON filing_contractors (object_id) WHERE is_management;

DROP VIEW IF EXISTS school_profile;
CREATE VIEW school_profile AS
SELECT s.ncessch, s.sch_name, s.lea_name, s.lcity, s.st, s.website AS school_website,
       st.status, st.reason, st.corrected, m.ein, m.tier, m.role, m.source, m.signals, m.ambiguous, m.evidence,
       f.object_id, f.org_name, f.return_type, f.tax_period_begin, f.tax_period_end,
       f.total_revenue, f.total_expenses, f.total_assets, f.total_liabilities, f.net_assets,
       f.mgmt_delegated, f.source_zip, f.amended
FROM schools s
LEFT JOIN school_status st ON st.ncessch = s.ncessch
LEFT JOIN school_matches m ON m.ncessch = s.ncessch
LEFT JOIN filings f ON f.object_id = m.object_id;
