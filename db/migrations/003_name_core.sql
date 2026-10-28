-- Name comparison helpers for matching.
--
-- name_core: a name key with generic school words removed, so that
-- "LAMAD ACADEMY CHARTER SCHOOL" and "CULTURAL ARTS ACADEMY CHARTER SCHOOL"
-- are compared on LAMAD versus CULTURAL ARTS rather than on shared filler.
-- Falls back to the full key when nothing distinctive is left.
CREATE OR REPLACE FUNCTION name_core(k text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT coalesce(
        nullif(btrim(regexp_replace(
            regexp_replace(
                ' ' || k || ' ',
                ' (CHARTER|CHARTERS|SCHOOL|SCHOOLS|ACADEMY|ACADEMIES|PUBLIC|PREPARATORY|PREP|COLLEGE|HIGH|MIDDLE|ELEMENTARY|COMMUNITY|LEARNING|CENTER|CENTRE|EDUCATION|EDUCATIONAL|FOUNDATION|INTERNATIONAL|OF|FOR|AND|AT|IN|CS|PCS)(?= )',
                ' ', 'g'),
            '\s+', ' ', 'g')), ''),
        k)
$$;

-- name_nums: the numbers in a name, such as "1" in ICAHN CHARTER SCHOOL 1.
-- Two names whose numbers differ belong to different schools.
CREATE OR REPLACE FUNCTION name_nums(k text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT btrim(regexp_replace(coalesce(k, ''), '[^0-9]+', ' ', 'g'))
$$;
