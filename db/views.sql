-- Views, re-created on every `make db` after the migrations. A view's
-- "SELECT *" is fixed when the view is created, so re-creating them here keeps
-- every view in step with its tables without a migration per new column.
-- (Migrations 001-009 created earlier versions; this file is now the source.)

-- The latest return per EIN: newest tax period, then the newest filing, which
-- picks an amended return over its original.
DROP VIEW IF EXISTS latest_filings;
CREATE VIEW latest_filings AS
SELECT DISTINCT ON (ein) *
FROM filings
ORDER BY ein, tax_period_end DESC NULLS LAST, return_ts DESC NULLS LAST, object_id DESC;

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
