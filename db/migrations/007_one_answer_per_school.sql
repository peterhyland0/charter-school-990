-- A school has at most one active "this nonprofit runs it" or "it has no
-- nonprofit" correction. Two would contradict each other, and which one won
-- would depend on row order. Unlinks are independent and may be several.
CREATE UNIQUE INDEX IF NOT EXISTS match_corrections_one_answer
    ON match_corrections (ncessch) WHERE retired_at IS NULL AND action IN ('link', 'none');
