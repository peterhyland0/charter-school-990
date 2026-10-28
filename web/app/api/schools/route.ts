import type { NextRequest } from "next/server";
import { toCsv } from "@/lib/csv";
import { propublicaUrl } from "@/lib/format";
import { filterQuery, listSchools, parseFilters, type ListRow } from "@/lib/schools";

// The school list as JSON (paged) or, with ?format=csv, as a spreadsheet of
// every matching school. Same filters as the /schools page.
const PAGE_SIZE = 500;

const num = (v: string | null) => (v === null ? null : Number(v));

function record(r: ListRow, origin: string) {
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
    const records = rows.map((r) => record(r, origin));
    const header = Object.keys(record({} as ListRow, origin));
    const body = toCsv(header, records.map((x) => Object.values(x)));
    const name = ["charter-schools", filters.st, filters.managed && `managed-${filters.managed}`, filters.status,
      filters.rev && `revenue-${filters.rev}`, new Date().toISOString().slice(0, 10)].filter(Boolean).join("-");
    return new Response(body, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="${name}.csv"`,
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
  });
}
