import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { sql } from "@/lib/db";
import { STATUS_CLASS, STATUS_LABEL } from "@/lib/labels";
import { describePlaces, listSchools, parseFilters, stateCodes } from "@/lib/schools";
import SearchBox from "./search-box";

const LIMIT = 50;

async function Summary() {
  // Request-time data: never query the database while building.
  await connection();
  const [row] = await sql<
    { total: string; verified: string; likely: string; unmatched: string; excluded: string; corrected: string }[]
  >`
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'verified') AS verified,
           count(*) FILTER (WHERE status = 'likely') AS likely,
           count(*) FILTER (WHERE status = 'unmatched') AS unmatched,
           -- Unmatched schools with no nonprofit to find: state law puts them under
           -- districts or makes them government bodies, state records say the
           -- district or the state runs them, or a government files their audit.
           count(*) FILTER (WHERE status = 'unmatched'
                            AND reason IN ('district_governed_state', 'public_body_state',
                                           'district_run_charter', 'government_auditee')) AS excluded,
           count(*) FILTER (WHERE corrected) AS corrected
    FROM school_status`;
  const places = describePlaces(await stateCodes());
  const n = (v: string) => Number(v).toLocaleString("en-US");
  return (
    <p className="mt-1 mb-4 text-sm opacity-70">
      {n(row.total)} operating charter schools in {places} · {n(row.verified)} verified ·{" "}
      {n(row.likely)} likely · {n(row.unmatched)} unmatched
      {Number(row.excluded) > 0 && ` (${n(row.excluded)} with no nonprofit to find)`}
      {Number(row.corrected) > 0 && ` · ${n(row.corrected)} corrected by hand`}
    </p>
  );
}

async function Search({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  await connection();
  const params = await searchParams;
  const filters = parseFilters(params);
  const { q, st } = filters;
  // The home search also finds schools by the nonprofit or management company
  // behind them, and includes closed schools, marked as such.
  const [states, { rows, total }] = await Promise.all([
    stateCodes(),
    q || st
      ? listSchools({ ...filters, status: "", managed: "", rev: "" }, { limit: LIMIT, includeClosed: true })
      : Promise.resolve({ rows: [], total: 0 }),
  ]);

  return (
    <>
      <form method="get" className="flex flex-wrap gap-2">
        <SearchBox
          key={q}
          defaultValue={q}
          placeholder="School, district, city, nonprofit or management company"
          className="rounded border border-black/20 bg-transparent px-3 py-2 dark:border-white/25"
        />
        <select
          name="st"
          defaultValue={st}
          aria-label="State"
          className="rounded border border-black/20 bg-transparent px-2 py-2 dark:border-white/25"
        >
          <option value="">All states</option>
          {states.map((s) => (
            <option key={s} value={s}>
              {s}
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
                {[
                  `${r.lcity}, ${r.st}`,
                  !r.is_operating && "not operating",
                  r.operators ? `Run by ${r.operators}` : r.is_operating && "No nonprofit matched",
                  r.managers && `Managed by ${r.managers}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
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
        Each school is linked to the nonprofit behind it, with leaders, pay and finances from its latest IRS Form 990.{" "}
        <Link href="/schools" className="underline">
          Build a list or download a spreadsheet →
        </Link>
      </p>
      <Suspense fallback={<p className="text-sm opacity-70">Loading…</p>}>
        <Search searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
