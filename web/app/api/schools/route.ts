import type { NextRequest } from "next/server";
import { toCsv } from "@/lib/csv";
import { propublicaUrl } from "@/lib/format";
import { API_CACHE, filterQuery, listSchools, num, parseFilters, type ListRow } from "@/lib/schools";

// The school list as JSON (paged) or, with ?format=csv, as a spreadsheet of
// every matching school. Same filters as the /schools page.
const PAGE_SIZE = 500;

const COLUMNS = [
  "ncessch", "school", "city", "state", "status", "unmatched_reason", "operator", "operator_ein",
  "operator_ambiguous", "management_company", "delegates_management", "revenue", "expenses", "net_assets",
  "top_paid_leader", "top_paid_leader_title", "top_paid_leader_pay", "year_ending", "return_form",
  "school_page", "return_url",
] as const;

function record(r: ListRow, origin: string): Record<(typeof COLUMNS)[number], string | number | boolean | null> {
  return {
    ncessch: r.ncessch,
    school: r.sch_name,
    city: r.lcity,
    state: r.st,
    status: r.status,
    unmatched_reason: r.reason,
    operator: r.operators,
    operator_ein: r.operator_eins,
    operator_ambiguous: r.ambiguous ?? false,
    management_company: r.managers,
    delegates_management: r.mgmt_delegated,
    revenue: num(r.total_revenue),
    expenses: num(r.total_expenses),
    net_assets: num(r.net_assets),
    top_paid_leader: r.leader_name,
    top_paid_leader_title: r.leader_title,
    top_paid_leader_pay: num(r.leader_pay),
    year_ending: r.tax_period_end ? new Date(r.tax_period_end).toISOString().slice(0, 10) : null,
    return_form: r.return_type,
    school_page: `${origin}/schools/${r.ncessch}`,
    return_url: r.ein && r.object_id ? propublicaUrl(r.ein, r.object_id) : null,
  };
}

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const filters = parseFilters(params);
  const origin = request.nextUrl.origin;

  if (params.get("format") === "csv") {
    const { rows } = await listSchools(filters);
    const body = toCsv([...COLUMNS], rows.map((r) => {
      const rec = record(r, origin);
      return COLUMNS.map((c) => rec[c]);
    }));
    const name = ["charter-schools", filters.st, filters.managed && `managed-${filters.managed}`, filters.status,
      filters.rev && `revenue-${filters.rev}`, new Date().toISOString().slice(0, 10)].filter(Boolean).join("-");
    return new Response(body, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="${name}.csv"`,
        "Cache-Control": API_CACHE,
      },
    });
  }

  const offset = Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0);
  const { rows, total } = await listSchools(filters, { limit: PAGE_SIZE, offset });
  const nextOffset = offset + rows.length;
  return Response.json({
    total,
    offset,
    schools: rows.map((r) => record(r, origin)),
    next: nextOffset < total ? `${origin}/api/schools?${filterQuery(filters, { offset: String(nextOffset) })}` : null,
  }, { headers: { "Cache-Control": API_CACHE } });
}
