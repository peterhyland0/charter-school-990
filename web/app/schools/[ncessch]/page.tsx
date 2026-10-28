import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";
import { sql } from "@/lib/db";
import { day, formatEin, money, month, nextReturn, propublicaUrl } from "@/lib/format";
import { reasonText, ROLE_LABEL, SIGNAL_LABEL, STATUS_CLASS, STATUS_LABEL, type Role, type Status } from "@/lib/labels";

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

type Contractor = {
  object_id: string;
  name: string | null;
  services: string | null;
  compensation: string | null;
  is_management: boolean;
};

// Where each fact sits on the return, so a rep can check it.
const LINES = {
  "990": { revenue: "Part I, line 12", expenses: "Part I, line 18", assets: "Part I, line 20", net: "Part I, line 22" },
  "990EZ": { revenue: "Part I, line 9", expenses: "Part I, line 17", assets: "Part II, line 25", net: "Part II, line 27" },
} as const;

const EXPLANATION_PREVIEW = 280;

// When the following return is due, in the right tense for today. The IRS
// index holds returns a few months after they're filed, so a past due date
// means the return isn't in the data yet.
function nextReturnText(next: NonNullable<ReturnType<typeof nextReturn>>): string {
  const now = new Date();
  const year = `The next return (year ending ${month(next.yearEnd)})`;
  if (now < next.due) {
    return `${year} is due ${day(next.due)}, or ${day(next.extended)} with the usual extension, and appears in IRS data a few months later.`;
  }
  if (now < next.extended) {
    return `${year} was due ${day(next.due)}; with the usual extension it is due ${day(next.extended)}. It isn't in IRS data yet.`;
  }
  return `${year} was due by ${day(next.extended)} at the latest, but isn't in IRS data yet.`;
}

// The management answer from three sources on the return, plus the matcher's
// linked management organization. Sources are shown side by side, not merged.
function ManagementCompany({ m, managers, contractors }: { m: Match; managers: Match[]; contractors: Contractor[] }) {
  if (m.return_type === "990EZ") {
    return <p className="text-sm">Not asked on Form 990-EZ.</p>;
  }
  const managing = contractors.filter((c) => c.is_management);
  const named = [...new Set([...managing.map((c) => c.name), ...managers.map((x) => x.org_name)].filter(Boolean))];
  const says = m.mgmt_delegated === null ? "Not answered" : m.mgmt_delegated ? "Yes" : "No";
  // Only Part VI line 3 says "yes" outright; a management contractor without it
  // is evidence, and the headline says so.
  const headline = m.mgmt_delegated
    ? named.length
      ? `Yes: ${named.join(" and ")}`
      : "Yes, management duties are delegated"
    : managing.length
      ? `The contractor list suggests yes: ${named.join(" and ")}`
      : says;
  const text = m.mgmt_explanation;
  return (
    <div className="text-sm">
      <p className="font-medium">{headline}</p>
      <ul className="mt-1 space-y-1 opacity-90">
        <li>
          <span className="opacity-70">Part VI, line 3 (delegates management?):</span> {says}
        </li>
        {managing.map((c, i) => (
          <li key={i}>
            <span className="opacity-70">Contractor list (Part VII, Section B):</span> {c.name}, {c.services?.toLowerCase()},{" "}
            {money(c.compensation)}
          </li>
        ))}
        {text && (
          <li>
            <span className="opacity-70">Explanation (Schedule O):</span>{" "}
            {text.length <= EXPLANATION_PREVIEW ? (
              <q>{text}</q>
            ) : (
              <details className="inline">
                <summary className="inline cursor-pointer list-none">
                  <q>{text.slice(0, EXPLANATION_PREVIEW).trimEnd()}…</q> <span className="underline">show more</span>
                </summary>
                <q className="mt-1 block whitespace-pre-line">{text}</q>
              </details>
            )}
          </li>
        )}
        {managers.map((x) => (
          <li key={x.ein}>
            <span className="opacity-70">Linked nonprofit:</span> {x.org_name}, the management organization this school is matched to
          </li>
        ))}
      </ul>
      {m.mgmt_delegated !== true && managing.length > 0 && (
        <p className="mt-1 rounded bg-amber-500/10 p-2">
          {m.mgmt_delegated === false
            ? "The sources disagree: the return says management isn't delegated, but lists a management contractor."
            : "The return leaves the delegation question unanswered, but lists a management contractor."}
        </p>
      )}
    </div>
  );
}

// Cards first for the nonprofits a rep acts on, then everything else.
const FULL_CARD: Role[] = ["operator", "management"];

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
    case "network_site":
      return `"${e.filing}" is the nonprofit on ${e.domain} whose name matches this school (similarity ${e.similarity})`;
    case "district_group":
      return `${e.verified_schools} other school${e.verified_schools === 1 ? "" : "s"} in ${e.district} verified to this nonprofit`;
    default:
      return "";
  }
}

async function SchoolDetail({ params }: { params: PageProps<"/schools/[ncessch]">["params"] }) {
  await connection();
  const { ncessch } = await params;
  const [school] = await sql<School[]>`SELECT * FROM schools WHERE ncessch = ${ncessch}`;
  if (!school) notFound();

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

  const cards = matches.filter((m) => FULL_CARD.includes(m.role));
  const others = matches.filter((m) => !FULL_CARD.includes(m.role));
  const managers = matches.filter((m) => m.role === "management");
  const operators = matches.filter((m) => m.role === "operator").length;

  const people = cards.length
    ? await sql<Person[]>`
        SELECT object_id, name, title, avg_hours, comp_org, comp_related, comp_other,
               (coalesce(comp_org, 0) + coalesce(comp_related, 0) + coalesce(comp_other, 0))::text AS total
        FROM filing_people
        WHERE object_id IN ${sql(cards.map((m) => m.object_id))}
        ORDER BY object_id, (coalesce(comp_org, 0) + coalesce(comp_related, 0) + coalesce(comp_other, 0)) DESC, seq`
    : [];

  const contractors = cards.length
    ? await sql<Contractor[]>`
        SELECT object_id, name, services, compensation, is_management
        FROM filing_contractors
        WHERE object_id IN ${sql(cards.map((m) => m.object_id))}
        ORDER BY object_id, compensation DESC NULLS LAST, seq`
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

      {operators > 1 && (
        <p className="mt-6 rounded bg-amber-500/10 p-3 text-sm">
          {operators} nonprofits match equally well and the data can&apos;t tell which one runs this school. Check
          the evidence below; a confirmed answer can be recorded as a hand correction.
        </p>
      )}

      {cards.map((m) => {
        const rows = people.filter((p) => p.object_id === m.object_id);
        const filedContractors = contractors.filter((c) => c.object_id === m.object_id);
        const lines = m.return_type === "990EZ" ? LINES["990EZ"] : LINES["990"];
        const next = nextReturn(m.tax_period_end);
        const correction = m.evidence.correction;
        return (
          <section key={m.ein} className="mt-6 rounded border border-black/15 p-4 dark:border-white/20">
            <p className="text-xs font-medium uppercase tracking-wide opacity-60">{ROLE_LABEL[m.role]}</p>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">{m.org_name}</h2>
              <span className="flex gap-2">
                {m.source === "corrected" && (
                  <span className="rounded bg-sky-600/15 px-2 py-0.5 text-xs text-sky-700 dark:text-sky-300">
                    Set by hand
                  </span>
                )}
                <span className={`rounded px-2 py-0.5 text-xs ${STATUS_CLASS[m.tier]}`}>
                  {STATUS_LABEL[m.tier]} match
                </span>
              </span>
            </div>
            {correction && (
              <p className="mt-1 text-sm opacity-80">
                Corrected by {correction.by} on {day(new Date(String(correction.at)))}: {correction.note}
              </p>
            )}
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

            <p className="mt-4 text-sm">
              <span className="opacity-70">Covers</span> {day(m.tax_period_begin)} – {day(m.tax_period_end)}
              {next && <span className="opacity-70"> · {nextReturnText(next)}</span>}
            </p>

            <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
              {(
                [
                  ["Revenue", m.total_revenue, lines.revenue],
                  ["Expenses", m.total_expenses, lines.expenses],
                  ["Total assets", m.total_assets, lines.assets],
                  ["Net assets", m.net_assets, lines.net],
                ] as const
              ).map(([label, value, line]) => (
                <div key={label}>
                  <dt className="opacity-70">{label}</dt>
                  <dd>{money(value)}</dd>
                  <dd className="text-xs opacity-50">{line}</dd>
                </div>
              ))}
            </dl>

            {m.role === "operator" && (
              <section className="mt-4">
                <h3 className="font-medium">Management company</h3>
                <ManagementCompany m={m} managers={managers} contractors={filedContractors} />
              </section>
            )}

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

            {filedContractors.length > 0 && (
              <details className="mt-4 text-sm">
                <summary className="cursor-pointer font-medium">
                  Largest contractors ({filedContractors.length}) <span className="text-xs font-normal opacity-50">Part VII, Section B</span>
                </summary>
                <ul className="mt-1 space-y-0.5">
                  {filedContractors.map((c, i) => (
                    <li key={i}>
                      {c.name} · {c.services?.toLowerCase()} · {money(c.compensation)}
                      {c.is_management && <span className="opacity-60"> · management</span>}
                    </li>
                  ))}
                </ul>
              </details>
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

      {others.length > 0 && (
        <section className="mt-6">
          <h2 className="font-semibold">Also linked</h2>
          <p className="text-sm opacity-70">Other nonprofits the evidence connects to this school, and how.</p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="opacity-70">
                <tr>
                  <th className="py-1 pr-3 font-medium">Nonprofit</th>
                  <th className="py-1 pr-3 font-medium">Role</th>
                  <th className="py-1 pr-3 font-medium">Evidence</th>
                  <th className="py-1 text-right font-medium">Revenue</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/10 dark:divide-white/10">
                {others.map((m) => (
                  <tr key={m.ein}>
                    <td className="py-1 pr-3">
                      <a href={propublicaUrl(m.ein, m.object_id)} className="underline" target="_blank" rel="noreferrer">
                        {m.org_name}
                      </a>
                      <span className="opacity-60"> · {formatEin(m.ein)}{m.filer_state && `, ${m.filer_state}`}</span>
                    </td>
                    <td className="py-1 pr-3">{ROLE_LABEL[m.role]}</td>
                    <td className="py-1 pr-3">
                      {STATUS_LABEL[m.tier]}: {m.signals.map((sig) => SIGNAL_LABEL[sig] ?? sig).join(", ")}
                    </td>
                    <td className="py-1 text-right">{money(m.total_revenue)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
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
