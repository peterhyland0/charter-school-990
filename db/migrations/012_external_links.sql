-- Evidence from outside the IRS data about who holds each charter school:
-- federal single audits (fac) and state charter-holder records (tx_tea,
-- la_doe, az_ccd). Rebuilt by the external_links asset, one source at a time.
CREATE TABLE IF NOT EXISTS external_links (
    ncessch      text NOT NULL,
    source       text NOT NULL CHECK (source IN ('fac', 'tx_tea', 'la_doe', 'az_ccd')),
    -- The holder's EIN, when the source gives one (fac).
    ein          text CHECK (ein ~ '^[0-9]{9}$'),
    holder_name  text NOT NULL,
    -- name_key of each legal name in holder_name (a DBA gives two).
    holder_keys  text[] NOT NULL DEFAULT '{}',
    -- fac: the auditee's self-reported type (non-profit, local, state, ...).
    entity_type  text,
    detail       jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS external_links_school ON external_links (ncessch);
CREATE INDEX IF NOT EXISTS external_links_ein ON external_links (ein) WHERE ein IS NOT NULL;

-- A third way into the candidate pool: named as a school's holder by one of the sources above.
ALTER TABLE bmf_orgs DROP CONSTRAINT IF EXISTS bmf_orgs_pool_route_check;
ALTER TABLE bmf_orgs ADD CONSTRAINT bmf_orgs_pool_route_check CHECK (pool_route IN ('codes', 'first_word', 'external'));
