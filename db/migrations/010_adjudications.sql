-- Decisions for schools the matcher can't settle: ambiguous schools (several
-- tied operators) and unmatched ones. Two methods decide from the same
-- evidence: 'scored' (hand-weighted features) and 'jev' (TypeSafe AI's Jev). A decision
-- is keyed by the hash of its input, so an unchanged case is never re-asked.
CREATE TABLE IF NOT EXISTS adjudications (
    ncessch     text NOT NULL,
    method      text NOT NULL CHECK (method IN ('scored', 'jev')),
    -- The scored model's version, or the Jev version that answered (e.g. jev-1.13.0).
    model       text NOT NULL,
    input_hash  text NOT NULL,
    -- NULL means none: no candidate is clearly the school's nonprofit.
    chosen_ein  text CHECK (chosen_ein ~ '^[0-9]{9}$'),
    confidence  real CHECK (confidence BETWEEN 0 AND 1),
    reason      text NOT NULL,
    decided_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ncessch, method, model, input_hash)
);
CREATE INDEX IF NOT EXISTS adjudications_latest ON adjudications (ncessch, method, decided_at DESC);

-- Applied decisions (ADJUDICATOR=jev|scored) are marked by their source.
ALTER TABLE school_matches DROP CONSTRAINT IF EXISTS school_matches_source_check;
ALTER TABLE school_matches ADD CONSTRAINT school_matches_source_check
    CHECK (source IN ('auto', 'corrected', 'jev', 'model'));
