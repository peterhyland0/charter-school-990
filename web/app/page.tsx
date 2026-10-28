import Link from "next/link";
import { Suspense } from "react";
import { sql } from "@/lib/db";

type Row = {
  ncessch: string;
  sch_name: string;
  lea_name: string | null;
  lcity: string | null;
  st: string;
  is_operating: boolean;
  nonprofits: string | null;
};

async function Search({ searchParams }: { searchParams: PageProps<"/">["searchParams"] }) {
  const params = await searchParams;
  const q = (typeof params.q === "string" ? params.q : "").trim();
  const pattern = `%${q}%`;
  const rows: Row[] = q
    ? await sql<Row[]>`
        SELECT s.ncessch, s.sch_name, s.lea_name, s.lcity, s.st, s.is_operating,
               (SELECT string_agg(DISTINCT f.org_name, '; ')
                  FROM school_matches m JOIN filings f ON f.object_id = m.object_id
                 WHERE m.ncessch = s.ncessch) AS nonprofits
        FROM schools s
        WHERE s.sch_name ILIKE ${pattern} OR s.lea_name ILIKE ${pattern} OR s.lcity ILIKE ${pattern}
        ORDER BY s.is_operating DESC, s.sch_name
        LIMIT 25`
    : [];

  return (
    <>
      <form method="get" className="flex gap-2">
        <input
          name="q"
          defaultValue={q}
          placeholder="School, district or city"
          className="flex-1 rounded border border-black/20 bg-transparent px-3 py-2 dark:border-white/25"
        />
        <button className="rounded bg-foreground px-4 py-2 text-background">Search</button>
      </form>
      {q && (
        <ul className="mt-6 divide-y divide-black/10 dark:divide-white/15">
          {rows.length === 0 && <li className="py-3 text-sm opacity-70">No schools match “{q}”.</li>}
          {rows.map((r) => (
            <li key={r.ncessch} className="py-3">
              <Link href={`/schools/${r.ncessch}`} className="font-medium underline-offset-2 hover:underline">
                {r.sch_name}
              </Link>
              <div className="text-sm opacity-70">
                {r.lcity}, {r.st}
                {!r.is_operating && " · not operating"}
                {" · "}
                {r.nonprofits ?? "No nonprofit matched yet"}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function Home({ searchParams }: PageProps<"/">) {
  return (
    <main>
      <h1 className="text-2xl font-semibold">Who runs this charter school?</h1>
      <p className="mt-1 mb-4 text-sm opacity-70">
        New York charter schools linked to the nonprofit behind each one, with leaders, pay and
        finances from its latest IRS Form 990.
      </p>
      <Suspense fallback={<p className="text-sm opacity-70">Loading…</p>}>
        <Search searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
