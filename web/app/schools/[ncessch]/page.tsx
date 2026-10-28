import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { sql } from "@/lib/db";
import { day, formatEin, money, propublicaUrl } from "@/lib/format";
import { reasonText, SIGNAL_LABEL, STATUS_CLASS, STATUS_LABEL, type Status } from "@/lib/labels";

type School = {
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

type Evidence = Record<string, Record<string, string | number | null>>;

type Match = {
  ein: string;
  tier: "verified" | "likely";
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
  mgmt_delegated: boolean | null;
  source_zip: string;
  filer_state: string | null;
};

type Person = {
  object_id: string;
  name: string | null;
  title: string | null;
  avg_hours: string | null;
  comp_org: string | null;
  comp_related: string | null;
  comp_other: string | null;
  total: string;
};

function managementLabel(m: Match): string {
  if (m.return_type === "990EZ") return "Not asked on Form 990-EZ";
  if (m.mgmt_delegated === null) return "Not answered";
  return m.mgmt_delegated ? "Yes, management duties are delegated" : "No";
}

type StatusRow = { status: Status; reason: string | null; detail: Record<string, string | boolean> };

// One line per agreeing signal, showing what was compared.
function signalDetail(signal: string, e: Record<string, string | number | null> | undefined): string {
  if (!e) return "";
  switch (signal) {
    case "website":
      return `both list ${e.domain}`;
    case "name_zip":
      return `"${e.filing}" in ZIP ${e.zip} (similarity ${e.similarity})`;
    case "name_city":
      return `"${e.filing}" in ${e.city} (similarity ${e.similarity})`;
    case "district_name":
      return `district "${e.district}" vs "${e.filing}" (similarity ${e.similarity})`;
    case "phone":
      return `both list ${String(e.phone).replace(/(\d{3})(\d{3})(\d{4})/, "($1) $2-$3")}`;
    default:
      return "";
  }
}

async function SchoolDetail({ params }: { params: PageProps<"/schools/[ncessch]">["params"] }) {
  const { ncessch } = await params;
  const [school] = await sql<School[]>`SELECT * FROM schools WHERE ncessch = ${ncessch}`;
  if (!school) notFound();

  const [status] = await sql<StatusRow[]>`SELECT status, reason, detail FROM school_status WHERE ncessch = ${ncessch}`;
  const matches = await sql<Match[]>`
    SELECT m.ein, m.tier, m.signals, m.ambiguous, m.evidence, f.object_id, f.org_name, f.return_type,
           f.tax_period_begin, f.tax_period_end, f.return_ts, f.amended,
           f.total_revenue, f.mgmt_delegated, f.source_zip, b.state AS filer_state
    FROM school_matches m
    JOIN filings f ON f.object_id = m.object_id
    LEFT JOIN bmf_orgs b ON b.ein = m.ein
    WHERE m.ncessch = ${ncessch}
    ORDER BY (m.tier = 'verified') DESC, cardinality(m.signals) DESC,
             (b.state = ${school.st}) DESC NULLS LAST, f.total_revenue DESC NULLS LAST`;

  const people = matches.length
    ? await sql<Person[]>`
        SELECT object_id, name, title, avg_hours, comp_org, comp_related, comp_other,
               (coalesce(comp_org, 0) + coalesce(comp_related, 0) + coalesce(comp_other, 0))::text AS total
        FROM filing_people
        WHERE object_id IN ${sql(matches.map((m) => m.object_id))}
        ORDER BY object_id, (coalesce(comp_org, 0) + coalesce(comp_related, 0) + coalesce(comp_other, 0)) DESC, seq`
    : [];

  return (
    <main>
      <Link href="/" className="text-sm opacity-70 hover:underline">← Search</Link>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{school.sch_name}</h1>
        {status && (
          <span className={`rounded px-2 py-0.5 text-sm ${STATUS_CLASS[status.status]}`}>
            {STATUS_LABEL[status.status]}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm opacity-70">
        {[school.lstreet1, school.lcity, school.st, school.lzip].filter(Boolean).join(", ")}
        {school.phone && ` · ${school.phone}`}
        {school.website && (
          <>
            {" · "}
            <a href={school.website.startsWith("http") ? school.website : `https://${school.website}`} className="underline">
              {school.website_domain ?? school.website}
            </a>
          </>
        )}
      </p>
      <p className="text-sm opacity-70">
        District: {school.lea_name} · NCES ID {school.ncessch}
        {!school.is_operating && " · not operating"}
      </p>

      {matches.length === 0 && (
        <section className="mt-6 rounded border border-black/15 p-4 dark:border-white/20">
          <h2 className="font-semibold">No nonprofit matched</h2>
          <p className="mt-1 text-sm opacity-80">
            {status ? reasonText(status.reason, status.detail) : "This school isn't operating, so it wasn't matched."}
          </p>
        </section>
      )}

      {matches.some((m) => m.ambiguous) && (
        <p className="mt-6 rounded bg-amber-500/10 p-3 text-sm">
          More than one nonprofit matches equally well. Often one runs the school and another is its network or
          management organization; check the signals below.
        </p>
      )}

      {matches.map((m) => {
        const rows = people.filter((p) => p.object_id === m.object_id);
        return (
          <section key={m.ein} className="mt-6 rounded border border-black/15 p-4 dark:border-white/20">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">{m.org_name}</h2>
              <span className={`rounded px-2 py-0.5 text-xs ${STATUS_CLASS[m.tier]}`}>
                {STATUS_LABEL[m.tier]} match
              </span>
            </div>
            <p className="text-sm opacity-70">
              EIN {formatEin(m.ein)}
              {m.filer_state && ` · registered in ${m.filer_state}`}
              {m.filer_state && m.filer_state !== school.st && " (out of state)"}
            </p>
            <ul className="mt-2 text-sm">
              {m.signals.map((sig) => (
                <li key={sig}>
                  <span className="font-medium">{SIGNAL_LABEL[sig] ?? sig}:</span> {signalDetail(sig, m.evidence[sig])}
                </li>
              ))}
            </ul>

            <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
              <div>
                <dt className="opacity-70">Period covered</dt>
                <dd>{day(m.tax_period_begin)} – {day(m.tax_period_end)}</dd>
              </div>
              <div>
                <dt className="opacity-70">Total revenue</dt>
                <dd>{money(m.total_revenue)}</dd>
              </div>
              <div>
                <dt className="opacity-70">Management company</dt>
                <dd>{managementLabel(m)}</dd>
              </div>
            </dl>

            <h3 className="mt-5 font-medium">Leaders and pay</h3>
            {rows.length === 0 ? (
              <p className="text-sm opacity-70">This return lists no officers or key employees.</p>
            ) : (
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="opacity-70">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Name</th>
                      <th className="py-1 pr-3 font-medium">Title</th>
                      <th className="py-1 pr-3 text-right font-medium">Hours/week</th>
                      <th className="py-1 pr-3 text-right font-medium">Pay from org</th>
                      <th className="py-1 pr-3 text-right font-medium">From related orgs</th>
                      <th className="py-1 pr-3 text-right font-medium">Other</th>
                      <th className="py-1 text-right font-medium">Total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-black/10 dark:divide-white/10">
                    {rows.map((p, i) => (
                      <tr key={i}>
                        <td className="py-1 pr-3">{p.name}</td>
                        <td className="py-1 pr-3">{p.title}</td>
                        <td className="py-1 pr-3 text-right">{p.avg_hours ? Number(p.avg_hours) : "—"}</td>
                        <td className="py-1 pr-3 text-right">{money(p.comp_org)}</td>
                        <td className="py-1 pr-3 text-right">{money(p.comp_related)}</td>
                        <td className="py-1 pr-3 text-right">{money(p.comp_other)}</td>
                        <td className="py-1 text-right font-medium">{money(p.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <p className="mt-4 text-xs opacity-70">
              Source: IRS Form {m.return_type === "990EZ" ? "990-EZ" : "990"}
              {m.amended && " (amended)"}, filed {day(m.return_ts)}, object ID {m.object_id} in {m.source_zip}.{" "}
              <a href={propublicaUrl(m.ein, m.object_id)} className="underline" target="_blank" rel="noreferrer">
                View return on ProPublica Nonprofit Explorer
              </a>
            </p>
          </section>
        );
      })}
    </main>
  );
}

export default function SchoolPage({ params }: PageProps<"/schools/[ncessch]">) {
  return (
    <Suspense fallback={<p className="text-sm opacity-70">Loading…</p>}>
      <SchoolDetail params={params} />
    </Suspense>
  );
}
