import type { NextRequest } from "next/server";
import { propublicaUrl } from "@/lib/format";
import { API_CACHE, num, schoolProfile } from "@/lib/schools";

// Everything the school page shows, as JSON. Unknown schools get a real 404.

export async function GET(request: NextRequest, ctx: RouteContext<"/api/schools/[ncessch]">) {
  const { ncessch } = await ctx.params;
  const profile = await schoolProfile(ncessch);
  if (!profile) return Response.json({ error: `No school with NCES id ${ncessch}` }, { status: 404 });
  const { school, status, matches, people, contractors, related } = profile;
  return Response.json({
    school,
    page: `${request.nextUrl.origin}/schools/${school.ncessch}`,
    status: status?.status ?? null,
    unmatched_reason: status?.reason ?? null,
    nonprofits: matches.map((m) => ({
      ein: m.ein,
      name: m.org_name,
      role: m.role,
      tier: m.tier,
      ambiguous: m.ambiguous,
      set_by_hand: m.source === "corrected",
      signals: m.signals,
      evidence: m.evidence,
      filing: {
        object_id: m.object_id,
        form: m.return_type,
        period_begin: m.tax_period_begin,
        period_end: m.tax_period_end,
        filed: m.return_ts,
        amended: m.amended,
        revenue: num(m.total_revenue),
        expenses: num(m.total_expenses),
        total_assets: num(m.total_assets),
        net_assets: num(m.net_assets),
        delegates_management: m.mgmt_delegated,
        management_explanation: m.mgmt_explanation,
        source_zip: m.source_zip,
        url: propublicaUrl(m.ein, m.object_id),
      },
      leaders: people.filter((p) => p.object_id === m.object_id).map((p) => ({
        name: p.name, title: p.title, hours_per_week: num(p.avg_hours),
        pay_from_org: num(p.comp_org), pay_from_related: num(p.comp_related), other_pay: num(p.comp_other),
        total_pay: num(p.total),
      })),
      contractors: contractors.filter((c) => c.object_id === m.object_id).map((c) => ({
        name: c.name, services: c.services, compensation: num(c.compensation), management_company: c.is_management,
      })),
      // Schedule R: management organizations and network members.
      related_organizations: related.filter((r) => r.ein === m.ein).map((r) => ({
        ein: r.related_ein, name: r.related_name, kind: r.kind, schools_run: r.related_schools,
        activity: r.evidence.activity ?? null, provides_services: r.evidence.services ?? false,
        services_amount: num(r.services_amt), shares_staff: r.evidence.shared_staff ?? false,
        listed_on: r.evidence.listed_on ?? null,
      })),
    })),
  }, { headers: { "Cache-Control": API_CACHE } });
}
