import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { sql } from "@/lib/db";
import { describePlaces, listSchools, parseFilters, stateCodes } from "@/lib/schools";
import RecentlyViewed from "./recently-viewed";
import SearchBox from "./search-box";
import SearchResults from "./search-results";

const LIMIT = 50;

// Searches that show what the box can do: a network, a school name and a
// management company.
const EXAMPLES = ["KIPP Texas", "Great Hearts", "Academica"];

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
    <p className="mt-1 text-sm text-gray-600">
      {n(row.total)} operating charter schools in {places} · {n(row.verified)} verified ·{" "}
      {n(row.likely)} likely · {n(row.unmatched)} unmatched
      {Number(row.excluded) > 0 && ` (${n(row.excluded)} with no nonprofit to find)`}
      {Number(row.corrected) > 0 && ` · ${n(row.corrected)} corrected by hand`}
    </p>
  );
}

async function SearchForm({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  await connection();
  const { q, st } = parseFilters(await searchParams);
  const states = await stateCodes();
  return (
    <form
      method="get"
      className="mt-5 flex flex-wrap gap-1.5 rounded-md border border-accent-line bg-surface p-1.5 focus-within:border-accent"
    >
      <SearchBox
        key={q}
        defaultValue={q}
        placeholder="School, district, city, nonprofit or management company"
        className="rounded-sm px-2.5 py-2 outline-none placeholder:text-muted"
      />
      <select name="st" defaultValue={st} aria-label="State" className="rounded-sm bg-gray-100 px-2 py-2 text-sm">
        <option value="">All states</option>
        {states.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <button className="rounded-sm bg-accent px-5 py-2 font-medium text-white hover:bg-accent-strong">Search</button>
    </form>
  );
}

async function Results({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  await connection();
  const filters = parseFilters(await searchParams);
  const { q, st } = filters;
  if (!q && !st) return <RecentlyViewed />;
  // The home search also finds schools by the nonprofit or management company
  // behind them, and includes closed schools, marked as such.
  const { rows, total } = await listSchools(
    { ...filters, status: "", managed: "", rev: "" },
    { limit: LIMIT, includeClosed: true },
  );

  return (
    <section className="mt-6">
      <p className="mb-2 text-sm">
        <span className="font-medium">
          {total.toLocaleString("en-US")} {total === 1 ? "school" : "schools"}
        </span>
        {total > rows.length && (
          <span className="text-muted">
            {" "}
            · showing the first {rows.length}. Add a city, school name or state to narrow it.
          </span>
        )}
      </p>
      {rows.length === 0 ? (
        <p className="rounded-md border border-line bg-surface px-4 py-6 text-sm text-muted">
          No schools match{st && ` in ${st}`}.
        </p>
      ) : (
        // Keyed by the search, so a new search starts back at the first result.
        <SearchResults
          key={`${q}|${st}`}
          rows={rows.map((r) => ({
            ncessch: r.ncessch,
            sch_name: r.sch_name,
            lcity: r.lcity,
            st: r.st,
            is_operating: r.is_operating,
            status: r.status,
            operators: r.operators,
            managers: r.managers,
          }))}
        />
      )}
    </section>
  );
}

export default function Home({ searchParams }: PageProps<"/">) {
  return (
    <main>
      <section className="rounded-md border border-accent-line/70 bg-accent-soft px-5 py-6 sm:px-7 sm:py-8">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Who runs this charter school?</h1>
        <Suspense fallback={<p className="mt-1 text-sm">&nbsp;</p>}>
          <Summary />
        </Suspense>
        <p className="mt-1 text-sm text-gray-600">
          Each school is linked to the nonprofit behind it, with leaders, pay and finances from its latest IRS Form 990.
        </p>
        <Suspense fallback={<div className="mt-5 h-[52px] rounded-md border border-accent-line bg-surface" />}>
          <SearchForm searchParams={searchParams} />
        </Suspense>
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">Try:</span>
          {EXAMPLES.map((example) => (
            <Link
              key={example}
              href={`/?q=${encodeURIComponent(example)}`}
              className="rounded-sm border border-accent-line bg-surface px-2 py-0.5 text-accent-strong hover:border-accent"
            >
              {example}
            </Link>
          ))}
          <Link href="/schools" className="ml-auto text-accent-strong hover:underline">
            Build a list or download a spreadsheet →
          </Link>
        </div>
      </section>
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Results searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
