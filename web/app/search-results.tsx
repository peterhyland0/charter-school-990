"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent } from "react";
import { STATUS_CLASS, STATUS_LABEL, type Status } from "@/lib/labels";
import type { Summary } from "@/lib/summary";
import SummaryTiles from "./summary-tiles";

export type ResultRow = {
  ncessch: string;
  sch_name: string;
  lcity: string | null;
  st: string;
  is_operating: boolean;
  status: Status | null;
  operators: string | null;
  managers: string | null;
};

type Preview = { district: string | null; summary: Summary | null; note: string | null };

// Wide enough for the list and the preview side by side: Tailwind's lg.
const WIDE = "(min-width: 1024px)";

function subscribeWide(onChange: () => void) {
  const query = matchMedia(WIDE);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

// The home search's results. On a wide screen a click or the arrow keys pick a
// school and its summary shows alongside, so a rep can scan without leaving the
// list; a second click, Enter or "Open full page" opens it. On a phone each
// result opens its page, as a plain link does.
export default function SearchResults({ rows }: { rows: ResultRow[] }) {
  // The server can't know the screen width, so it renders the plain list and
  // the preview appears once the browser has loaded the page.
  const wide = useSyncExternalStore(subscribeWide, () => matchMedia(WIDE).matches, () => false);
  const [selected, setSelected] = useState(0);
  const [previews, setPreviews] = useState<Record<string, Preview | "error">>({});
  const links = useRef<(HTMLAnchorElement | null)[]>([]);
  const current = rows[selected];
  const id = current?.ncessch;

  // Fetch the picked school's summary once, and drop it if the reader has
  // already moved on.
  useEffect(() => {
    if (!wide || !id || previews[id]) return;
    const controller = new AbortController();
    fetch(`/api/schools/${id}/summary`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((preview: Preview) => setPreviews((all) => ({ ...all, [id]: preview })))
      .catch(() => {
        if (!controller.signal.aborted) setPreviews((all) => ({ ...all, [id]: "error" }));
      });
    return () => controller.abort();
  }, [wide, id, previews]);

  function pick(next: number) {
    const i = Math.min(rows.length - 1, Math.max(0, next));
    setSelected(i);
    links.current[i]?.focus();
  }

  // With nothing focused, the first arrow press moves into the list.
  useEffect(() => {
    if (!wide) return;
    function onKey(e: globalThis.KeyboardEvent) {
      if ((e.key !== "ArrowDown" && e.key !== "ArrowUp") || document.activeElement !== document.body) return;
      e.preventDefault();
      links.current[selected]?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [wide, selected]);

  function onListKey(e: KeyboardEvent<HTMLUListElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    pick(selected + (e.key === "ArrowDown" ? 1 : -1));
  }

  // A plain click picks; a click on the picked school, or one meant for a new
  // tab, follows the link.
  function onClick(e: MouseEvent<HTMLAnchorElement>, i: number) {
    if (!wide || i === selected || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    setSelected(i);
  }

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:items-start lg:gap-4">
      <div>
        <ul onKeyDown={onListKey} className="divide-y divide-line rounded-md border border-line bg-surface">
          {rows.map((r, i) => {
            const picked = wide && i === selected;
            return (
              <li key={r.ncessch}>
                <Link
                  ref={(el) => {
                    links.current[i] = el;
                  }}
                  href={`/schools/${r.ncessch}`}
                  onClick={(e) => onClick(e, i)}
                  aria-current={picked ? "true" : undefined}
                  className={`group flex items-center justify-between gap-3 px-4 py-3 outline-none focus-visible:bg-accent-soft ${
                    picked ? "bg-accent-soft shadow-[inset_3px_0_0_var(--color-accent)]" : "hover:bg-accent-soft/50"
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium group-hover:text-accent">{r.sch_name}</span>
                      {r.status && (
                        <span className={`rounded-sm px-1.5 py-0.5 text-xs font-medium ${STATUS_CLASS[r.status]}`}>
                          {STATUS_LABEL[r.status]}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-muted">
                      {[
                        `${r.lcity}, ${r.st}`,
                        !r.is_operating && "not operating",
                        r.operators ? `Run by ${r.operators}` : r.is_operating && "No nonprofit matched",
                        r.managers && `Managed by ${r.managers}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  </div>
                  <svg
                    viewBox="0 0 16 16"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    className={`size-4 shrink-0 group-hover:text-accent ${picked ? "text-accent" : "text-gray-300"}`}
                    aria-hidden="true"
                  >
                    <path d="m6 3.5 4.5 4.5L6 12.5" />
                  </svg>
                </Link>
              </li>
            );
          })}
        </ul>
        <p className="mt-2 hidden text-xs text-muted lg:block">
          <Key>↑</Key> <Key>↓</Key> to move through the list · <Key>Enter</Key> or a second click opens a school
        </p>
      </div>
      <aside aria-label="Preview" className="hidden lg:sticky lg:top-4 lg:block">
        {wide && current && <PreviewPanel row={current} preview={previews[current.ncessch]} />}
      </aside>
    </div>
  );
}

function Key({ children }: { children: string }) {
  return <kbd className="rounded-xs border border-line-strong bg-surface px-1 font-mono">{children}</kbd>;
}

function PreviewPanel({ row, preview }: { row: ResultRow; preview: Preview | "error" | undefined }) {
  const loaded = preview && preview !== "error" ? preview : null;
  return (
    <section className="rounded-md border border-line bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-tight">{row.sch_name}</h2>
          <p className="text-sm text-muted">
            {[`${row.lcity}, ${row.st}`, !row.is_operating && "not operating", loaded?.district && `District: ${loaded.district}`]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {row.status && (
          <span className={`shrink-0 rounded-sm px-2 py-0.5 text-sm font-medium ${STATUS_CLASS[row.status]}`}>
            {STATUS_LABEL[row.status]}
          </span>
        )}
      </div>
      <div className="mt-4">
        {preview === undefined ? (
          <div className="grid grid-cols-2 gap-2" aria-label="Loading the summary">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-[78px] animate-pulse rounded-md border border-line bg-gray-50" />
            ))}
          </div>
        ) : preview === "error" ? (
          <p className="text-sm text-muted">The summary didn&apos;t load. The full page has everything.</p>
        ) : preview.summary ? (
          <SummaryTiles summary={preview.summary} narrow />
        ) : (
          <p className="text-sm text-gray-700">
            {preview.note ?? "No nonprofit is matched as running this school. The full page shows what the evidence found."}
          </p>
        )}
      </div>
      <Link
        href={`/schools/${row.ncessch}`}
        className="mt-4 inline-flex rounded-sm bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-strong"
      >
        Open full page →
      </Link>
    </section>
  );
}
