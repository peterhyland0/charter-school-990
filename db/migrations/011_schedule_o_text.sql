-- Parser 0.7: every Schedule O entry ("<line reference>: <explanation>"),
-- capped at 8,000 characters. The adjudicators read it for the schools a
-- nonprofit names. latest_filings picks the column up when db/views.sql
-- re-creates it.
ALTER TABLE filings ADD COLUMN IF NOT EXISTS schedule_o_text text;
