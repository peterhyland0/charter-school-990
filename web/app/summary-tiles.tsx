import type { Summary } from "@/lib/summary";

// The summary tiles and the return they come from. The school page (a server
// component) and the search preview (a client component) both render this, so
// it has no "use client" and imports nothing server-only.
export default function SummaryTiles({ summary, narrow = false }: { summary: Summary; narrow?: boolean }) {
  return (
    <>
      <dl className={`grid grid-cols-2 gap-2 ${narrow ? "" : "lg:grid-cols-4"}`}>
        {summary.tiles.map((t) => (
          <div key={t.label} className="rounded-md border border-line bg-surface px-4 py-3">
            <dt className="text-xs text-muted">{t.label}</dt>
            <dd className="mt-0.5 font-semibold break-words">{t.value}</dd>
            <dd className="mt-0.5 text-xs text-muted">{t.hint}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted">
        From the Form {summary.form} for the year ending {summary.yearEnding}.{" "}
        <a href={summary.returnUrl} className="text-accent-strong underline" target="_blank" rel="noreferrer">
          View the return
        </a>
      </p>
    </>
  );
}
