-- Schedule R: the organisations each filer is related to by control, and the
-- money that moved between them. Rebuilt by the related_orgs asset.

-- Part II (related tax-exempt organisations), Part III (related partnerships)
-- and Part IV (related corporations and trusts).
CREATE TABLE IF NOT EXISTS filing_related_orgs (
    object_id      text NOT NULL REFERENCES filings (object_id) ON DELETE CASCADE,
    seq            int  NOT NULL,
    part           text NOT NULL CHECK (part IN ('II', 'III', 'IV')),
    name           text,
    name_key       text,
    ein            text,
    state          text,
    activity       text,
    -- The entity that directly controls the related organisation, as the
    -- filer wrote it; NULL when the filer answered "N/A".
    controlled_by  text,
    -- A controlled entity under section 512(b)(13): the filer controls it.
    controlled     boolean,
    PRIMARY KEY (object_id, seq)
);
CREATE INDEX IF NOT EXISTS filing_related_orgs_ein ON filing_related_orgs (ein) WHERE ein IS NOT NULL;

-- Part V line 2: transactions with related organisations, named but not by EIN.
-- type holds the form's letters (a to s), several when the filer combined
-- them: l = services for the related organisation, m = services by it,
-- o = shared paid employees, and so on.
CREATE TABLE IF NOT EXISTS filing_related_transactions (
    object_id  text NOT NULL REFERENCES filings (object_id) ON DELETE CASCADE,
    seq        int  NOT NULL,
    name       text,
    name_key   text,
    type       text,
    amount     bigint,
    PRIMARY KEY (object_id, seq)
);

-- What Schedule R says about each nonprofit that runs schools, from its own
-- return or the related organisation's. Rebuilt by the related_links asset.
--   management  the related organisation performs services for this one, or
--               is described as its management organisation
--   network     a related nonprofit that runs or supports charter schools
CREATE TABLE IF NOT EXISTS related_links (
    ein              text NOT NULL,
    related_ein      text NOT NULL,
    related_name     text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('management', 'network')),
    -- Part V: services by the related organisation (letter m on this return,
    -- l on theirs), the larger figure when both returns report it.
    services_amt     bigint,
    -- Operating schools the related organisation itself runs, per the matcher.
    related_schools  int NOT NULL DEFAULT 0,
    evidence         jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (ein, related_ein)
);
CREATE INDEX IF NOT EXISTS related_links_related ON related_links (related_ein);
