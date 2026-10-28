import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { sql } from "@/lib/db";
import { STATUS_CLASS, STATUS_LABEL, type Status } from "@/lib/labels";

type Row = {
  ncessch: string;
  sch_name: string;
  lcity: string | null;
  st: string;
  is_operating: boolean;
  status: Status | null;
  nonprofits: string | null;
  managers: string | null;
  total: string;
};

const LIMIT = 50;

// Treat the user's % and _ as literal characters, not LIKE wildcards.
function likePattern(q: string): string {
  return `%${q.replace(/[\\%_]/g, "\\$&")}%`;
}

async function Summary() {
  // Request-time data: never query the database while building.
  await connection();
  const [row] = await sql<
    { total: string; verified: string; likely: string; unmatched: string; corrected: string; states: string }[]
  >`
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'verified') AS verified,
           count(*) FILTER (WHERE status = 'likely') AS likely,
           count(*) FILTER (WHERE status = 'unmatched') AS unmatched,
           count(*) FILTER (WHERE corrected) AS corrected,
           (SELECT count(DISTINCT st) FROM schools WHERE is_operating) AS states
    FROM school_status`;
  const n = (v: string) => Number(v).toLocaleString("en-US");
  return (
    <p className="mt-1 mb-4 text-sm opacity-70">
      {n(row.total)} operating charter schools in {row.states} states · {n(row.verified)} verified ·{" "}
      {n(row.likely)} likely · {n(row.unmatched)} unmatched
      {Number(row.corrected) > 0 && ` · ${n(row.corrected)} corrected by hand`}
    </p>
  );
}

async function Search({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  await connection();
  const params = await searchParams;
  const q = (typeof params.q === "string" ? params.q : "").trim();
  const st = typeof params.st === "string" ? params.st.trim().toUpperCase() : "";
  const states = await sql<{ st: string }[]>`SELECT DISTINCT st FROM schools WHERE is_operating ORDER BY st`;
  const pattern = likePattern(q);

  const rows: Row[] =
    q || st
      ? await sql<Row[]>`
          SELECT s.ncessch, s.sch_name, s.lcity, s.st, s.is_operating, ss.status,
                 -- The nonprofit(s) that run the school; several when it's ambiguous.
                 (SELECT string_agg(DISTINCT f.org_name, ' or ')
                    FROM school_matches m JOIN filings f ON f.object_id = m.object_id
                   WHERE m.ncessch = s.ncessch AND m.role = 'operator') AS nonprofits,
                 -- Its management company: a management contractor on the
                 -- operator's return, or else the linked management nonprofit.
                 coalesce(
                   (SELECT string_agg(DISTINCT c.name, ' and ')
                      FROM school_matches m JOIN filing_contractors c ON c.object_id = m.object_id
                     WHERE m.ncessch = s.ncessch AND m.role = 'operator' AND c.is_management),
                   (SELECT string_agg(DISTINCT f.org_name, ' and ')
                      FROM school_matches m JOIN filings f ON f.object_id = m.object_id
                     WHERE m.ncessch = s.ncessch AND m.role = 'management')
                 ) AS managers,
                 count(*) OVER () AS total
          FROM schools s
          LEFT JOIN school_status ss ON ss.ncessch = s.ncessch
          WHERE (${q} = '' OR s.sch_name ILIKE ${pattern} OR s.lea_name ILIKE ${pattern} OR s.lcity ILIKE ${pattern})
            AND (${st} = '' OR s.st = ${st})
          ORDER BY s.is_operating DESC, s.sch_name
          LIMIT ${LIMIT}`
      : [];
  const total = rows.length ? Number(rows[0].total) : 0;

  return (
    <>
      <form method="get" className="flex flex-wrap gap-2">
        <input
          name="q"
          defaultValue={q}
          placeholder="School, district or city"
          className="min-w-0 flex-1 rounded border border-black/20 bg-transparent px-3 py-2 dark:border-white/25"
        />
        <select
          name="st"
          defaultValue={st}
          aria-label="State"
          className="rounded border border-black/20 bg-transparent px-2 py-2 dark:border-white/25"
        >
          <option value="">All states</option>
          {states.map((s) => (
            <option key={s.st} value={s.st}>
              {s.st}
            </option>
          ))}
        </select>
        <button className="rounded bg-foreground px-4 py-2 text-background">Search</button>
      </form>
      {total > rows.length && (
        <p className="mt-4 text-sm opacity-70">
          Showing the first {rows.length} of {total.toLocaleString("en-US")} schools. Add a city, school name or state to
          narrow it.
        </p>
      )}
      {(q || st) && (
        <ul className="mt-4 divide-y divide-black/10 dark:divide-white/15">
          {rows.length === 0 && <li className="py-3 text-sm opacity-70">No schools match.</li>}
          {rows.map((r) => (
            <li key={r.ncessch} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/schools/${r.ncessch}`} className="font-medium underline-offset-2 hover:underline">
                  {r.sch_name}
                </Link>
                {r.status && (
                  <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_CLASS[r.status]}`}>{STATUS_LABEL[r.status]}</span>
                )}
              </div>
              <div className="text-sm opacity-70">
                {r.lcity}, {r.st}
                {!r.is_operating && " · not operating"}
                {" · "}
                {r.nonprofits ? `Run by ${r.nonprofits}` : "No nonprofit matched"}
                {r.managers && ` · Managed by ${r.managers}`}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function Home({ searchParams }: PageProps<"/">) {
  return (
    <main>
      <h1 className="text-2xl font-semibold">Who runs this charter school?</h1>
      <Suspense fallback={<p className="mt-1 mb-4 text-sm opacity-70">&nbsp;</p>}>
        <Summary />
      </Suspense>
      <p className="mb-4 text-sm opacity-70">
        Each school is linked to the nonprofit behind it, with leaders, pay and finances from its latest IRS Form 990.
      </p>
      <Suspense fallback={<p className="text-sm opacity-70">Loading…</p>}>
        <Search searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
