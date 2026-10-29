-- Trigram indexes for the website's search and its suggestions, which look for
-- the typed text anywhere in a nonprofit's legal or trade name. Without them
-- every keystroke scans the whole filings table.
CREATE INDEX IF NOT EXISTS filings_org_name_trgm ON filings USING gin (org_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS filings_dba_name_trgm ON filings USING gin (dba_name gin_trgm_ops);
