import "server-only";
import { cache } from "react";
import { sql } from "@/lib/db";
import type { Role, Status } from "@/lib/labels";

// Every query the site and its exports make, so the list page, the CSV, the
// JSON and the school page can't drift apart.

// --- Filters -----------------------------------------------------------------

// Operator revenue bands, in dollars, cut near the quartiles ($4.4M, $10.4M,
// $32M) and the 90th percentile ($202M).
export const REVENUE_BANDS = {
  "0-5": { label: "Under $5M", min: 0, max: 5_000_000 },
  "5-25": { label: "$5M–$25M", min: 5_000_000, max: 25_000_000 },
  "25-100": { label: "$25M–$100M", min: 25_000_000, max: 100_000_000 },
  "100-": { label: "Over $100M", min: 100_000_000, max: null },
} as const;
type RevenueBand = keyof typeof REVENUE_BANDS;

const STATUSES: Status[] = ["verified", "likely", "unmatched"];

export type Filters = {
  q: string;
  st: string;
  status: Status | "";
  managed: "yes" | "no" | "";
  rev: RevenueBand | "";
};

type Params = URLSearchParams | Record<string, string | string[] | undefined>;

function param(params: Params, name: string): string {
  const value = params instanceof URLSearchParams ? params.get(name) : params[name];
  return (Array.isArray(value) ? value[0] : value ?? "").trim();
}

// Unknown values are ignored rather than rejected, so a mistyped link still
// shows a list.
export function parseFilters(params: Params): Filters {
  const status = param(params, "status");
  const managed = param(params, "managed");
  const rev = param(params, "rev");
  const st = param(params, "st").toUpperCase();
  return {
    q: param(params, "q"),
    st: /^[A-Z]{2}$/.test(st) ? st : "",
    status: STATUSES.includes(status as Status) ? (status as Status) : "",
    managed: managed === "yes" || managed === "no" ? managed : "",
    rev: rev in REVENUE_BANDS ? (rev as RevenueBand) : "",
  };
}

export function filterQuery(filters: Filters, extra: Record<string, string> = {}): string {
  const entries = Object.entries({ ...filters, ...extra }).filter(([, v]) => v !== "");
  return new URLSearchParams(entries).toString();
}

// Treat the user's % and _ as literal characters, not LIKE wildcards.
function likePattern(q: string): string {
  return `%${q.replace(/[\\%_]/g, "\\$&")}%`;
}

// --- List ----------------------------------------------------------------------

export type ListRow = {
  ncessch: string;
  sch_name: string;
  lcity: string | null;
  st: string;
  is_operating: boolean;
  status: Status | null;
  reason: string | null;
  operators: string | null;
  operator_eins: string | null;
  ambiguous: boolean | null;
  ein: string | null;
  object_id: string | null;
  return_type: string | null;
  total_revenue: string | null;
  total_expenses: string | null;
  net_assets: string | null;
  mgmt_delegated: boolean | null;
  has_explanation: boolean | null;
  tax_period_end: Date | null;
  managers: string | null;
  leader_name: string | null;
  leader_title: string | null;
  leader_pay: string | null;
  total: string;
};

// One row per school. The operator's return fills the row; when several
// nonprofits could be the operator, they're listed with "or" and the facts come
// from the largest by revenue.
export async function listSchools(
  filters: Filters,
  { limit, offset = 0, includeClosed = false }: { limit?: number; offset?: number; includeClosed?: boolean } = {},
): Promise<{ rows: ListRow[]; total: number }> {
  const pattern = likePattern(filters.q);
  const band = filters.rev ? REVENUE_BANDS[filters.rev] : null;
  const managed = sql`(f.mgmt_delegated IS TRUE OR mg.managers IS NOT NULL)`;
  // Filter first, then page, then look up the top-paid leader for the page's
  // rows only. The outer join keeps one row carrying the total even when the
  // page is past the end, so the count is never reported as zero by mistake.
  const result = await sql<(ListRow | (Partial<ListRow> & { ncessch: null; total: string }))[]>`
    WITH matched AS (
      SELECT s.ncessch, s.sch_name, s.lcity, s.st, s.is_operating, ss.status, ss.reason,
             o.operators, o.operator_eins, o.ambiguous,
             f.ein, f.object_id, f.return_type, f.total_revenue, f.total_expenses, f.net_assets,
             f.mgmt_delegated, f.mgmt_explanation IS NOT NULL AS has_explanation, f.tax_period_end,
             mg.managers
      FROM schools s
      LEFT JOIN school_status ss ON ss.ncessch = s.ncessch
      LEFT JOIN LATERAL (
        SELECT string_agg(mf.org_name, ' or ' ORDER BY mf.total_revenue DESC NULLS LAST) AS operators,
               string_agg(m.ein, ' or ' ORDER BY mf.total_revenue DESC NULLS LAST) AS operator_eins,
               (array_agg(m.object_id ORDER BY mf.total_revenue DESC NULLS LAST))[1] AS object_id,
               count(*) > 1 AS ambiguous
        FROM school_matches m JOIN filings mf ON mf.object_id = m.object_id
        WHERE m.ncessch = s.ncessch AND m.role = 'operator'
      ) o ON true
      LEFT JOIN filings f ON f.object_id = o.object_id
      LEFT JOIN LATERAL (
        -- The management company: a management contractor on the operator's
        -- return, or else the linked management nonprofit, or else a related
        -- management organization on Schedule R.
        SELECT coalesce(
          (SELECT string_agg(DISTINCT c.name, ' and ') FROM filing_contractors c
            WHERE c.object_id = o.object_id AND c.is_management),
          (SELECT string_agg(DISTINCT xf.org_name, ' and ') FROM school_matches x
             JOIN filings xf ON xf.object_id = x.object_id
            WHERE x.ncessch = s.ncessch AND x.role = 'management'),
          (SELECT string_agg(DISTINCT l.related_name, ' and ') FROM related_links l
            WHERE l.ein = f.ein AND l.kind = 'management')
        ) AS managers
      ) mg ON true
      WHERE ${includeClosed ? sql`true` : sql`s.is_operating`}
        ${filters.st ? sql`AND s.st = ${filters.st}` : sql``}
        ${filters.status ? sql`AND ss.status = ${filters.status}` : sql``}
        ${filters.managed === "yes" ? sql`AND ${managed}` : sql``}
        ${filters.managed === "no" ? sql`AND o.object_id IS NOT NULL AND NOT ${managed}` : sql``}
        ${band ? sql`AND f.total_revenue >= ${band.min}` : sql``}
        ${band?.max ? sql`AND f.total_revenue < ${band.max}` : sql``}
        ${filters.q ? sql`AND (
          s.sch_name ILIKE ${pattern} OR s.lea_name ILIKE ${pattern} OR s.lcity ILIKE ${pattern}
          -- The nonprofit or management company behind the school, so
          -- "Academica" or "KIPP Texas" finds the schools they run.
          OR EXISTS (SELECT 1 FROM school_matches m JOIN filings nf ON nf.object_id = m.object_id
                     WHERE m.ncessch = s.ncessch AND m.role IN ('operator', 'management')
                       AND (nf.org_name ILIKE ${pattern} OR nf.dba_name ILIKE ${pattern}))
          OR EXISTS (SELECT 1 FROM school_matches m JOIN filing_contractors c ON c.object_id = m.object_id
                     WHERE m.ncessch = s.ncessch AND m.role = 'operator' AND c.is_management
                       AND c.name ILIKE ${pattern})
          OR EXISTS (SELECT 1 FROM school_matches m JOIN related_links l ON l.ein = m.ein
                     WHERE m.ncessch = s.ncessch AND m.role = 'operator' AND l.kind = 'management'
                       AND l.related_name ILIKE ${pattern}))` : sql``}
    ),
    page AS (
      SELECT * FROM matched
      ORDER BY is_operating DESC, st, sch_name, ncessch
      ${limit ? sql`LIMIT ${limit} OFFSET ${offset}` : sql``}
    )
    SELECT p.*, lp.name AS leader_name, lp.title AS leader_title, lp.total::text AS leader_pay,
           (SELECT count(*) FROM matched) AS total
    FROM (SELECT 1) AS one
    LEFT JOIN page p ON true
    LEFT JOIN LATERAL (
      -- The highest-paid person on the return. Volunteers (all pay zero) don't
      -- count: where a management company employs the staff, the return lists
      -- only an unpaid board, and "top-paid leader: $0" would mislead.
      SELECT x.name, x.title, x.total FROM (
        SELECT fp.name, fp.title, fp.seq,
               coalesce(fp.comp_org, 0) + coalesce(fp.comp_related, 0) + coalesce(fp.comp_other, 0) AS total
        FROM filing_people fp WHERE fp.object_id = p.object_id
      ) x
      WHERE x.total > 0
      ORDER BY x.total DESC, x.seq LIMIT 1
    ) lp ON true
    ORDER BY p.is_operating DESC, p.st, p.sch_name, p.ncessch`;
  const total = result.length ? Number(result[0].total) : 0;
  return { rows: result.filter((r): r is ListRow => r.ncessch !== null), total };
}

// The same list of codes is needed by several components of one page: query once per request.
export const stateCodes = cache(async (): Promise<string[]> => {
  const rows = await sql<{ st: string }[]>`SELECT DISTINCT st FROM schools WHERE is_operating ORDER BY st`;
  return rows.map((r) => r.st);
});

// The data changes when the monthly pipeline runs, so the API lets Vercel's CDN
// serve repeat requests for an hour (and a stale copy for a day while it
// refreshes) instead of rerunning the national query for each one.
export const API_CACHE = "public, s-maxage=3600, stale-while-revalidate=86400";

// postgres.js returns bigint and numeric columns as strings; JSON and CSV want numbers.
export const num = (v: string | null | undefined): number | null => (v === null || v === undefined ? null : Number(v));

// --- One school ---------------------------------------------------------------

export type School = {
  ncessch: string;
  sch_name: string;
  lea_name: string | null;
  lstreet1: string | null;
  lcity: string | null;
  st: string;
  lzip: string | null;
  phone: string | null;
  website: string | null;
  website_domain: string | null;
  is_operating: boolean;
};

export type Evidence = Record<string, Record<string, string | number | null>>;

export type Match = {
  ein: string;
  tier: "verified" | "likely";
  role: Role;
  source: "auto" | "corrected";
  signals: string[];
  ambiguous: boolean;
  evidence: Evidence;
  object_id: string;
  org_name: string | null;
  return_type: string;
  tax_period_begin: Date | null;
  tax_period_end: Date | null;
  return_ts: Date | null;
  amended: boolean;
  total_revenue: string | null;
  total_expenses: string | null;
  total_assets: string | null;
  net_assets: string | null;
  mgmt_delegated: boolean | null;
  mgmt_explanation: string | null;
  source_zip: string;
  filer_state: string | null;
};

export type Person = {
  object_id: string;
  name: string | null;
  title: string | null;
  avg_hours: string | null;
  comp_org: string | null;
  comp_related: string | null;
  comp_other: string | null;
  total: string;
};

export type Contractor = {
  object_id: string;
  name: string | null;
  services: string | null;
  compensation: string | null;
  is_management: boolean;
};

// What Schedule R says about an operator: its management organization or a
// fellow nonprofit in its network (pipeline/src/charter990/assets/related.py).
export type Related = {
  ein: string;
  related_ein: string;
  related_name: string;
  kind: "management" | "network";
  services_amt: string | null;
  related_schools: number;
  evidence: { listed_on?: string; activity?: string; services?: boolean; shared_staff?: boolean };
};

export type StatusRow = { status: Status; reason: string | null; detail: Record<string, string | boolean> };

export type Profile = {
  school: School;
  status: StatusRow | null;
  matches: Match[];
  people: Person[];
  contractors: Contractor[];
  related: Related[];
};

// Roles a rep acts on get the full facts (leaders, contractors).
export const FULL_CARD: Role[] = ["operator", "management"];

export async function schoolProfile(ncessch: string): Promise<Profile | null> {
  const [school] = await sql<School[]>`
    SELECT ncessch, sch_name, lea_name, lstreet1, lcity, st, lzip, phone, website, website_domain, is_operating
    FROM schools WHERE ncessch = ${ncessch}`;
  if (!school) return null;

  const [status] = await sql<StatusRow[]>`SELECT status, reason, detail FROM school_status WHERE ncessch = ${ncessch}`;
  const matches = await sql<Match[]>`
    SELECT m.ein, m.tier, m.role, m.source, m.signals, m.ambiguous, m.evidence, f.object_id, f.org_name, f.return_type,
           f.tax_period_begin, f.tax_period_end, f.return_ts, f.amended,
           f.total_revenue, f.total_expenses, f.total_assets, f.net_assets,
           f.mgmt_delegated, f.mgmt_explanation, f.source_zip, b.state AS filer_state
    FROM school_matches m
    JOIN filings f ON f.object_id = m.object_id
    LEFT JOIN bmf_orgs b ON b.ein = m.ein
    WHERE m.ncessch = ${ncessch}
    ORDER BY array_position(ARRAY['operator', 'management', 'support', 'affiliate', 'related', 'other'], m.role),
             (m.tier = 'verified') DESC, cardinality(m.signals) DESC,
             (b.state = ${school.st}) DESC NULLS LAST, f.total_revenue DESC NULLS LAST`;

  const cardIds = matches.filter((m) => FULL_CARD.includes(m.role)).map((m) => m.object_id);
  const people = cardIds.length
    ? await sql<Person[]>`
        SELECT object_id, name, title, avg_hours, comp_org, comp_related, comp_other,
               (coalesce(comp_org, 0) + coalesce(comp_related, 0) + coalesce(comp_other, 0))::text AS total
        FROM filing_people
        WHERE object_id IN ${sql(cardIds)}
        ORDER BY object_id, (coalesce(comp_org, 0) + coalesce(comp_related, 0) + coalesce(comp_other, 0)) DESC, seq`
    : [];
  const contractors = cardIds.length
    ? await sql<Contractor[]>`
        SELECT object_id, name, services, compensation, is_management
        FROM filing_contractors
        WHERE object_id IN ${sql(cardIds)}
        ORDER BY object_id, compensation DESC NULLS LAST, seq`
    : [];
  const operatorEins = matches.filter((m) => m.role === "operator").map((m) => m.ein);
  const related = operatorEins.length
    ? await sql<Related[]>`
        SELECT ein, related_ein, related_name, kind, services_amt, related_schools, evidence
        FROM related_links
        WHERE ein IN ${sql(operatorEins)}
        ORDER BY kind, services_amt DESC NULLS LAST, related_schools DESC, related_name`
    : [];

  return { school, status: status ?? null, matches, people, contractors, related };
}

// --- Summary ------------------------------------------------------------------

const PLACE_NAMES: Record<string, string> = {
  DC: "DC", PR: "Puerto Rico", GU: "Guam", VI: "US Virgin Islands", AS: "American Samoa", MP: "Northern Mariana Islands",
};

// "45 states, DC, Puerto Rico and Guam": states counted, the rest named.
export function describePlaces(codes: string[]): string {
  const states = codes.filter((c) => !(c in PLACE_NAMES)).length;
  const others = codes.filter((c) => c in PLACE_NAMES).map((c) => PLACE_NAMES[c]);
  const parts = [`${states} states`, ...others];
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
