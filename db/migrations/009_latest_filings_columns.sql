-- A view's "SELECT *" is fixed when the view is created, so latest_filings
-- needs recreating to include the columns migration 008 added to filings.
CREATE OR REPLACE VIEW latest_filings AS
SELECT DISTINCT ON (ein) *
FROM filings
ORDER BY ein, tax_period_end DESC NULLS LAST, return_ts DESC NULLS LAST, object_id DESC;
