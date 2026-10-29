import { reasonText } from "@/lib/labels";
import { API_CACHE, schoolProfile } from "@/lib/schools";
import { schoolSummary } from "@/lib/summary";

// The school page's summary tiles alone, for the preview beside the home
// search's results (app/search-results.tsx): much smaller than the full JSON.
export async function GET(_request: Request, ctx: RouteContext<"/api/schools/[ncessch]/summary">) {
  const { ncessch } = await ctx.params;
  const profile = await schoolProfile(ncessch);
  if (!profile) return Response.json({ error: `No school with NCES id ${ncessch}` }, { status: 404 });
  const { school, status, matches } = profile;
  return Response.json(
    {
      district: school.lea_name,
      summary: schoolSummary(profile),
      // Where no nonprofit matched, the reason the school page gives.
      note: !matches.length && status ? reasonText(status.reason, status.detail) : null,
    },
    { headers: { "Cache-Control": API_CACHE } },
  );
}
