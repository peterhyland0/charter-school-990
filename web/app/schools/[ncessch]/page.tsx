import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { sql } from "@/lib/db";
import { day, formatEin, money, propublicaUrl } from "@/lib/format";

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

type Match = {
  ein: string;
  tier: "domain_single" | "domain_multi";
  evidence: { domain: string; school_website: string | null; filing_website: string | null };
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

function tierLabel(m: Match, count: number): string {
  return m.tier === "domain_single"
    ? "Matched by website"
    : `Matched by website, shared with ${count - 1} other nonprofit${count > 2 ? "s" : ""}`;
}

async function SchoolDetail({ params }: { params: PageProps<"/schools/[ncessch]">["params"] }) {
  const { ncessch } = await params;
  const [school] = await sql<School[]>`SELECT * FROM schools WHERE ncessch = ${ncessch}`;
  if (!school) notFound();

  const matches = await sql<Match[]>`
    SELECT m.ein, m.tier, m.evidence, f.object_id, f.org_name, f.return_type,
           f.tax_period_begin, f.tax_period_end, f.return_ts, f.amended,
           f.total_revenue, f.mgmt_delegated, f.source_zip, b.state AS filer_state
    FROM school_matches m
    JOIN filings f ON f.object_id = m.object_id
    LEFT JOIN bmf_orgs b ON b.ein = m.ein
    WHERE m.ncessch = ${ncessch}
    ORDER BY (b.state = ${school.st}) DESC NULLS LAST, f.total_revenue DESC NULLS LAST`;

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
      <h1 className="mt-2 text-2xl font-semibold">{school.sch_name}</h1>
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
          <h2 className="font-semibold">No nonprofit matched yet</h2>
          <p className="mt-1 text-sm opacity-80">
            {school.website_domain
              ? `No candidate nonprofit's latest return lists this school's website domain (${school.website_domain}).`
              : "This school has no website in the NCES directory, so the website match can't run."}
          </p>
        </section>
      )}

      {matches.map((m) => {
        const rows = people.filter((p) => p.object_id === m.object_id);
        return (
          <section key={m.ein} className="mt-6 rounded border border-black/15 p-4 dark:border-white/20">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">{m.org_name}</h2>
              <span className="rounded bg-black/5 px-2 py-0.5 text-xs dark:bg-white/10">
                {tierLabel(m, matches.length)}
              </span>
            </div>
            <p className="text-sm opacity-70">
              EIN {formatEin(m.ein)}
              {m.filer_state && ` · registered in ${m.filer_state}`}
              {m.filer_state && m.filer_state !== school.st && " (out of state)"} · school site {m.evidence.school_website} and the return&apos;s site{" "}
              {m.evidence.filing_website} share {m.evidence.domain}
            </p>

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
