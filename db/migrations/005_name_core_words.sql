-- name_core now takes its list of generic words as an argument, so the list
-- lives in one place (GENERIC_WORDS in pipeline/src/charter990/util.py) and the
-- matcher passes it in, instead of SQL and Python each keeping their own copy.
DROP FUNCTION IF EXISTS name_core(text);

CREATE OR REPLACE FUNCTION name_core(k text, generic text[]) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT coalesce(
        nullif(btrim(regexp_replace(
            regexp_replace(
                ' ' || k || ' ',
                ' (' || array_to_string(generic, '|') || ')(?= )',
                ' ', 'g'),
            '\s+', ' ', 'g')), ''),
        k)
$$;
