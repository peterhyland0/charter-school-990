import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";
import { day, formatEin, money, month, nextReturn, propublicaUrl } from "@/lib/format";
import { reasonText, ROLE_LABEL, SIGNAL_LABEL, STATUS_CLASS, STATUS_LABEL } from "@/lib/labels";
import { FULL_CARD, schoolProfile, type Contractor, type Match, type Related } from "@/lib/schools";

// Where each fact sits on the return, so a rep can check it.
const LINES = {
  "990": { revenue: "Part I, line 12", expenses: "Part I, line 18", assets: "Part I, line 20", net: "Part I, line 22" },
  "990EZ": { revenue: "Part I, line 9", expenses: "Part I, line 17", assets: "Part II, line 25", net: "Part II, line 27" },
} as const;

const EXPLANATION_PREVIEW = 280;
// Network members shown before "and N more".
const NETWORK_PREVIEW = 8;

// What Schedule R says about one related organization, and which return says it.
function relatedDetail(r: Related): string {
  const e = r.evidence;
  const parts = [];
  if (e.services) {
    parts.push(r.services_amt ? `provided ${money(r.services_amt)} of services (Part V)` : "provided services (Part V)");
  }
  if (e.activity) parts.push(`described as "${e.activity.toLowerCase()}"`);
  if (e.shared_staff) parts.push("shares paid staff");
  if (r.related_schools > 0) parts.push(`runs ${r.related_schools} school${r.related_schools === 1 ? "" : "s"} here`);
  if (e.listed_on) parts.push(`listed on ${e.listed_on}`);
  return parts.join(", ");
}

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

// The management answer from four sources on the returns, plus the matcher's
// linked management organization. Sources are shown side by side, not merged.
function ManagementCompany({ m, managers, contractors, related }: {
  m: Match; managers: Match[]; contractors: Contractor[]; related: Related[];
}) {
  if (m.return_type === "990EZ") {
    return <p className="text-sm">Not asked on Form 990-EZ.</p>;
  }
  const managing = contractors.filter((c) => c.is_management);
  const scheduleR = related.filter((r) => r.kind === "management");
  const named = [...new Set([
    ...managing.map((c) => c.name), ...managers.map((x) => x.org_name), ...scheduleR.map((r) => r.related_name),
  ].filter(Boolean))];
  const says = m.mgmt_delegated === null ? "Not answered" : m.mgmt_delegated ? "Yes" : "No";
  // Only Part VI line 3 says "yes" outright; a management contractor or a
  // related management organization without it is evidence, and the headline
  // says so.
  const headline = m.mgmt_delegated
    ? named.length
      ? `Yes: ${named.join(" and ")}`
      : "Yes, management duties are delegated"
    : managing.length
      ? `The contractor list suggests yes: ${named.join(" and ")}`
      : scheduleR.length
        ? `Schedule R suggests yes: ${named.join(" and ")}`
        : says;
  const other = managing.length
    ? "lists a management contractor"
    : "Schedule R shows a related organization that manages it or provides its services";
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
        {scheduleR.map((r) => (
          <li key={r.related_ein}>
            <span className="opacity-70">Related organization (Schedule R):</span> {r.related_name}, EIN{" "}
            {formatEin(r.related_ein)}: {relatedDetail(r)}
          </li>
        ))}
        {managers.map((x) => (
          <li key={x.ein}>
            <span className="opacity-70">Linked nonprofit:</span> {x.org_name}, the management organization this school is matched to
          </li>
        ))}
      </ul>
      {m.mgmt_delegated !== true && (managing.length > 0 || scheduleR.length > 0) && (
        <p className="mt-1 rounded bg-amber-500/10 p-2">
          {m.mgmt_delegated === false
            ? `The sources disagree: the return says management isn't delegated, but ${other}.`
            : `The return leaves the delegation question unanswered, but ${other}.`}
        </p>
      )}
    </div>
  );
}

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
  const profile = await schoolProfile(ncessch);
  if (!profile) notFound();
  const { school, status, matches, people, contractors, related } = profile;

  const cards = matches.filter((m) => FULL_CARD.includes(m.role));
  const others = matches.filter((m) => !FULL_CARD.includes(m.role));
  const managers = matches.filter((m) => m.role === "management");
  const operators = matches.filter((m) => m.role === "operator").length;
  const network = related.filter((r) => r.kind === "network");

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
        const members = network.filter((r) => r.ein === m.ein);
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
                <ManagementCompany
                  m={m}
                  managers={managers}
                  contractors={filedContractors}
                  related={related.filter((r) => r.ein === m.ein)}
                />
              </section>
            )}

            {m.role === "operator" && members.length > 0 && (
              <section className="mt-4 text-sm">
                <h3 className="font-medium">
                  Network <span className="text-xs font-normal opacity-50">Schedule R</span>
                </h3>
                <p className="opacity-70">Related nonprofits that run or support charter schools, as the returns list them.</p>
                <ul className="mt-1 space-y-0.5">
                  {members.slice(0, NETWORK_PREVIEW).map((r) => (
                    <li key={r.related_ein}>
                      {r.related_name} <span className="opacity-60">· {formatEin(r.related_ein)} · {relatedDetail(r)}</span>
                    </li>
                  ))}
                </ul>
                {members.length > NETWORK_PREVIEW && <p className="opacity-60">and {members.length - NETWORK_PREVIEW} more</p>}
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
