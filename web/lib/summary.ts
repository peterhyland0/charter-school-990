import { compactMoney, formatEin, month, propublicaUrl } from "@/lib/format";
import type { Contractor, Match, Profile, Related } from "@/lib/schools";

// The four answers a rep came for, from the operator's latest return: who runs
// the school, how big it is, who is paid most and whether a management company
// is involved. The school page and the search preview both show these.

export type Tile = { label: string; value: string; hint: string };
export type Summary = { tiles: Tile[]; form: string; yearEnding: string; returnUrl: string };

// The management organizations the evidence names: a management contractor on
// the return, the linked management nonprofit, or Schedule R.
export function namedManagers(contractors: Contractor[], managers: Match[], related: Related[]): string[] {
  return [...new Set([
    ...contractors.filter((c) => c.is_management).map((c) => c.name),
    ...managers.map((x) => x.org_name),
    ...related.filter((r) => r.kind === "management").map((r) => r.related_name),
  ].filter((name): name is string => Boolean(name)))];
}

// The management tile: the named company if the evidence names one, else what
// Part VI, line 3 says.
function managementTile(m: Match, named: string[]): [string, string] {
  if (m.return_type === "990EZ") return ["Not asked", "Form 990-EZ has no such question"];
  if (named.length) {
    return [named.join(" and "), m.mgmt_delegated ? "The return says management is delegated" : "Named in the return's evidence"];
  }
  if (m.mgmt_delegated) return ["Delegated, not named", "The return says management is delegated"];
  return [m.mgmt_delegated === false ? "None reported" : "Not answered", "Part VI, line 3 of the return"];
}

// Null when no nonprofit is matched as the operator.
export function schoolSummary({ matches, people, contractors, related }: Profile): Summary | null {
  const operators = matches.filter((m) => m.role === "operator");
  const m = operators[0];
  if (!m) return null;
  const managers = matches.filter((x) => x.role === "management");
  const named = namedManagers(
    contractors.filter((c) => c.object_id === m.object_id),
    managers,
    related.filter((r) => r.ein === m.ein),
  );
  // Volunteers (all pay zero) don't count, as in the school list.
  const leader = people.find((p) => p.object_id === m.object_id && Number(p.total) > 0);
  const listed = people.some((p) => p.object_id === m.object_id);
  const others = operators.length - 1;
  const [management, managementHint] = managementTile(m, named);
  return {
    tiles: [
      {
        label: "Run by",
        value: m.org_name ?? "—",
        hint: others > 0
          ? `Or ${others} other ${others === 1 ? "nonprofit that matches" : "nonprofits that match"} equally well`
          : `EIN ${formatEin(m.ein)}`,
      },
      { label: "Revenue", value: compactMoney(m.total_revenue), hint: `Net assets ${compactMoney(m.net_assets)}` },
      {
        label: "Top-paid leader",
        value: leader ? compactMoney(leader.total) : listed ? "None paid" : "—",
        hint: leader
          ? [leader.name, leader.title].filter(Boolean).join(", ")
          : listed ? "The return lists only unpaid officers" : "The return lists no officers",
      },
      { label: "Management company", value: management, hint: managementHint },
    ],
    form: m.return_type === "990EZ" ? "990-EZ" : "990",
    yearEnding: month(m.tax_period_end),
    returnUrl: propublicaUrl(m.ein, m.object_id),
  };
}
