-- Phase 0 schema. Safe to apply repeatedly.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-- Charter schools from the NCES Common Core of Data school directory.
CREATE TABLE IF NOT EXISTS schools (
    ncessch         text PRIMARY KEY,
    schid           text,
    leaid           text,
    lea_name        text,
    st_schid        text,
    sch_name        text NOT NULL,
    st              text NOT NULL,
    lstreet1        text,
    lcity           text,
    lzip            text,
    phone           text,
    website         text,
    website_host    text,
    website_domain  text,
    chartauthn1     text,
    updated_status  int,
    is_operating    boolean NOT NULL,
    source_file     text NOT NULL
);

-- Candidate nonprofits from the IRS Exempt Organizations Business Master File.
-- Every code is text so leading zeros survive.
CREATE TABLE IF NOT EXISTS bmf_orgs (
    ein             text PRIMARY KEY CHECK (ein ~ '^[0-9]{9}$'),
    name            text NOT NULL,
    sort_name       text,
    city            text,
    state           text,
    zip             text,
    subsection      text,
    foundation      text,
    affiliation     text,
    filing_req_cd   text,
    ntee_cd         text,
    pool_route      text NOT NULL CHECK (pool_route IN ('codes', 'first_word')),
    source_file     text NOT NULL
);

-- Rows of the IRS yearly e-file index, filtered to candidate EINs.
CREATE TABLE IF NOT EXISTS irs_index (
    object_id       text PRIMARY KEY,
    ein             text NOT NULL,
    tax_period      text NOT NULL,
    return_type     text NOT NULL,
    taxpayer_name   text,
    xml_batch_id    text NOT NULL,
    return_id       text,
    dln             text,
    index_year      int NOT NULL
);
CREATE INDEX IF NOT EXISTS irs_index_ein ON irs_index (ein);

-- Which zip file physically holds each return. The index label is not a locator.
CREATE TABLE IF NOT EXISTS zip_members (
    object_id       text PRIMARY KEY,
    zip_name        text NOT NULL,
    zip_url         text NOT NULL,
    zip_etag        text
);

-- One row per parsed return.
CREATE TABLE IF NOT EXISTS filings (
    object_id         text PRIMARY KEY,
    ein               text NOT NULL,
    return_type       text NOT NULL,
    return_version    text,
    tax_period_begin  date,
    tax_period_end    date,
    return_ts         timestamptz,
    amended           boolean NOT NULL DEFAULT false,
    org_name          text,
    dba_name          text,
    website           text,
    website_host      text,
    website_domain    text,
    total_revenue     bigint,
    mgmt_delegated    boolean,
    source_zip        text NOT NULL,
    sha256            text NOT NULL,
    parser_version    text NOT NULL,
    parsed_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS filings_ein ON filings (ein);
CREATE INDEX IF NOT EXISTS filings_domain ON filings (website_domain);

-- Officers, directors, key employees and highest-paid staff from each return.
CREATE TABLE IF NOT EXISTS filing_people (
    object_id        text NOT NULL REFERENCES filings (object_id) ON DELETE CASCADE,
    seq              int NOT NULL,
    name             text,
    title            text,
    avg_hours        numeric,
    comp_org         bigint,
    comp_related     bigint,
    comp_other       bigint,
    is_officer       boolean NOT NULL DEFAULT false,
    is_director      boolean NOT NULL DEFAULT false,
    is_key_employee  boolean NOT NULL DEFAULT false,
    is_hce           boolean NOT NULL DEFAULT false,
    PRIMARY KEY (object_id, seq)
);

-- School-to-nonprofit links with the evidence behind each.
CREATE TABLE IF NOT EXISTS school_matches (
    ncessch     text NOT NULL REFERENCES schools (ncessch),
    ein         text NOT NULL,
    object_id   text NOT NULL,
    method      text NOT NULL,
    tier        text NOT NULL CHECK (tier IN ('domain_single', 'domain_multi')),
    evidence    jsonb NOT NULL,
    matched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ncessch, ein)
);

-- Anything that failed to download or parse, so runs carry on.
CREATE TABLE IF NOT EXISTS load_errors (
    id     bigserial PRIMARY KEY,
    stage  text NOT NULL,
    key    text NOT NULL,
    error  text NOT NULL,
    at     timestamptz NOT NULL DEFAULT now()
);

-- Latest return per EIN: newest period first, then newest submission,
-- which picks an amended return over the original it replaces.
CREATE OR REPLACE VIEW latest_filings AS
SELECT DISTINCT ON (ein) *
FROM filings
ORDER BY ein, tax_period_end DESC NULLS LAST, return_ts DESC NULLS LAST, object_id DESC;

CREATE OR REPLACE VIEW school_profile AS
SELECT s.ncessch, s.sch_name, s.lea_name, s.lcity, s.st, s.website AS school_website,
       s.is_operating, m.ein, m.tier, m.method, m.evidence,
       f.object_id, f.org_name, f.return_type, f.tax_period_begin, f.tax_period_end,
       f.total_revenue, f.mgmt_delegated, f.source_zip, f.amended
FROM schools s
LEFT JOIN school_matches m ON m.ncessch = s.ncessch
LEFT JOIN filings f ON f.object_id = m.object_id;
