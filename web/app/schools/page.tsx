import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { money, month } from "@/lib/format";
import { STATUS_CLASS, STATUS_LABEL } from "@/lib/labels";
import { filterQuery, listSchools, parseFilters, REVENUE_BANDS, stateCodes, type Filters } from "@/lib/schools";

// Territory lists: filter, scan, download. Filters live in the URL so a list
// can be bookmarked or shared.
const PAGE_SIZE = 100;

function FilterBar({ filters, states }: { filters: Filters; states: string[] }) {
  const field = "rounded-sm border border-line-strong bg-surface px-2 py-1.5 focus:border-accent focus:outline-none";
  return (
    <form method="get" className="flex flex-wrap items-end gap-2 rounded-md border border-line bg-surface p-3 text-sm">
      <label className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-muted">Search</span>
        <input id="q" name="q" defaultValue={filters.q} placeholder="School, city, nonprofit or manager" className={field} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-muted">State</span>
        <select id="st" name="st" defaultValue={filters.st} className={field}>
          <option value="">All</option>
          {states.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-muted">Match</span>
        <select id="status" name="status" defaultValue={filters.status} className={field}>
          <option value="">All</option>
          <option value="verified">Verified</option>
          <option value="likely">Likely</option>
          <option value="unmatched">Unmatched</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-muted">Management company</span>
        <select id="managed" name="managed" defaultValue={filters.managed} className={field}>
          <option value="">Any</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-muted">Operator revenue</span>
        <select id="rev" name="rev" defaultValue={filters.rev} className={field}>
          <option value="">Any</option>
          {Object.entries(REVENUE_BANDS).map(([k, b]) => <option key={k} value={k}>{b.label}</option>)}
        </select>
      </label>
      <button className="rounded-sm bg-accent px-4 py-1.5 font-medium text-white hover:bg-accent-strong">Show</button>
    </form>
  );
}

async function SchoolList({ searchParams }: { searchParams: PageProps<"/schools">["searchParams"] }) {
  await connection();
  const params = await searchParams;
  const filters = parseFilters(params);
  const page = Math.max(1, Number.parseInt(typeof params.page === "string" ? params.page : "1", 10) || 1);
  const [states, { rows, total }] = await Promise.all([
    stateCodes(),
    listSchools(filters, { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
  ]);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const query = filterQuery(filters);
  const pageLink = (n: number) => `/schools?${filterQuery(filters, n > 1 ? { page: String(n) } : {})}`;

  return (
    <>
      <FilterBar filters={filters} states={states} />
      <div className="mt-4 flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <p>
          <span className="font-medium">{total.toLocaleString("en-US")} schools</span>
          {pages > 1 && <span className="text-muted"> · page {page} of {pages}</span>}
        </p>
        {total > 0 && (
          <a href={`/api/schools?${query ? `${query}&` : ""}format=csv`} className="text-accent-strong underline">
            Download CSV ({total.toLocaleString("en-US")} rows)
          </a>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-muted">
          {total > 0 ? `This page is past the end of the list.` : "No schools match these filters."}
        </p>
      ) : (
        <div className="mt-2 overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="border-b border-line bg-gray-50 text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">School</th>
                <th className="px-3 py-2 font-medium">Run by</th>
                <th className="px-3 py-2 font-medium">Management company</th>
                <th className="px-3 py-2 text-right font-medium">Revenue</th>
                <th className="px-3 py-2 font-medium">Top-paid leader</th>
                <th className="px-3 py-2 font-medium">Year ending</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line align-top">
              {rows.map((r) => (
                <tr key={r.ncessch}>
                  <td className="px-3 py-2">
                    <Link href={`/schools/${r.ncessch}`} className="font-medium underline-offset-2 hover:text-accent hover:underline">
                      {r.sch_name}
                    </Link>
                    <div className="text-muted">
                      {r.lcity}, {r.st}
                      {r.status && (
                        <span className={`ml-2 rounded-sm px-1.5 font-medium py-0.5 text-xs ${STATUS_CLASS[r.status]}`}>
                          {STATUS_LABEL[r.status]}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {r.operators ?? <span className="text-muted">No nonprofit matched</span>}
                    {r.ambiguous && <div className="text-xs text-muted">ambiguous</div>}
                  </td>
                  <td className="px-3 py-2">
                    {r.managers ??
                      (r.mgmt_delegated ? (
                        r.has_explanation ? "Delegated (see the return's explanation)" : "Delegated, manager not named"
                      ) : (
                        <span className="text-muted">—</span>
                      ))}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{money(r.total_revenue)}</td>
                  <td className="px-3 py-2">
                    {r.leader_name ? (
                      <>
                        {r.leader_name}
                        <div className="text-xs text-muted">
                          {r.leader_title} · {money(r.leader_pay)}
                        </div>
                      </>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{month(r.tax_period_end)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(pages > 1 || page > pages) && (
        <nav className="mt-4 flex gap-4 text-sm">
          {page > pages ? (
            <Link href={pageLink(pages)} className="text-accent-strong underline">← Back to page {pages}</Link>
          ) : (
            page > 1 && <Link href={pageLink(page - 1)} className="text-accent-strong underline">← Previous</Link>
          )}
          {page < pages && <Link href={pageLink(page + 1)} className="text-accent-strong underline">Next →</Link>}
        </nav>
      )}
    </>
  );
}

export default function SchoolsPage({ searchParams }: PageProps<"/schools">) {
  return (
    <main>
      <h1 className="text-2xl font-semibold">School lists</h1>
      <p className="mt-1 mb-4 text-sm text-muted">
        Build a territory list: filter, then download it as a spreadsheet. One row per operating charter school.
      </p>
      <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
        <SchoolList searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
