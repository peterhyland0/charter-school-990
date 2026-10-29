import type { NextRequest } from "next/server";
import { API_CACHE, parseFilters, suggest } from "@/lib/schools";

// Suggestions for the home search box (app/search-box.tsx). The box asks once
// per pause in typing, and the CDN answers repeats of a prefix.
export async function GET(request: NextRequest) {
  const { q, st } = parseFilters(request.nextUrl.searchParams);
  const term = q.slice(0, 100);
  const body = term.length >= 2 ? await suggest(term, st) : { schools: [], orgs: [] };
  return Response.json(body, { headers: { "Cache-Control": API_CACHE } });
}
