"use client";

import Link from "next/link";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import { STATUS_CLASS, STATUS_LABEL, type Status } from "@/lib/labels";

// The schools this browser opened last, newest first. Kept in localStorage, so
// it never leaves the browser and needs no account. Storage can be blocked
// (private windows, cleared site data); then the list just stays empty.
const KEY = "recently-viewed";
const LIMIT = 6;
// localStorage's own event only reaches other tabs; this one tells this tab.
const CHANGED = "recently-viewed-change";

type Viewed = { ncessch: string; name: string; place: string; status: Status | null; operator: string | null; at: number };

function readRaw(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

// Whatever is stored was written by an older version of this page or edited by
// hand, so keep only entries that still have the expected shape.
function parse(raw: string | null): Viewed[] {
  try {
    const list: unknown = JSON.parse(raw ?? "[]");
    return Array.isArray(list)
      ? list.filter(
          (v): v is Viewed =>
            typeof v?.ncessch === "string" && /^\d+$/.test(v.ncessch) && typeof v.name === "string" && typeof v.at === "number",
        )
      : [];
  } catch {
    return [];
  }
}

function save(list: Viewed[]) {
  try {
    if (list.length) localStorage.setItem(KEY, JSON.stringify(list));
    else localStorage.removeItem(KEY);
  } catch {
    // Storage blocked: nothing to remember.
  }
  window.dispatchEvent(new Event(CHANGED));
}

// The stored list and when it was read, which the "2 hours ago" times count
// from. The same object comes back until the list changes, as React requires.
type Snapshot = { raw: string | null; now: number };
const EMPTY: Snapshot = { raw: null, now: 0 };
let snapshot = EMPTY;

function getSnapshot(): Snapshot {
  const raw = readRaw();
  if (raw !== snapshot.raw) snapshot = { raw, now: Date.now() };
  return snapshot;
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

function ago(at: number, now: number): string {
  const minutes = Math.floor((now - at) / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  if (hours < 48) return "Yesterday";
  return new Date(at).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// Placed on a school's page: moves the school to the top of the list.
export function RememberView({ school }: { school: Omit<Viewed, "at"> }) {
  const { ncessch, name, place, status, operator } = school;
  useEffect(() => {
    const rest = parse(readRaw()).filter((v) => v.ncessch !== ncessch);
    save([{ ncessch, name, place, status, operator, at: Date.now() }, ...rest].slice(0, LIMIT));
  }, [ncessch, name, place, status, operator]);
  return null;
}

// The home page's list. The server has no storage to read, so it renders
// nothing there and the list appears once the browser has loaded the page.
export default function RecentlyViewed() {
  const { raw, now } = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY);
  const list = useMemo(() => parse(raw), [raw]);
  if (!list.length) return null;

  return (
    <section className="mt-6 rounded-md border border-line bg-surface">
      <div className="flex items-center justify-between gap-2 px-4 py-3">
        <h2 className="flex items-center gap-2 font-semibold">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="size-4 text-accent" aria-hidden="true">
            <path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9M2.5 2.5v2.5H5M8 5v3l2 1.5" />
          </svg>
          Recently viewed
        </h2>
        <button type="button" onClick={() => save([])} className="text-xs text-muted underline hover:text-ink">
          Clear
        </button>
      </div>
      <ul className="divide-y divide-line border-t border-line">
        {list.map((v) => (
          <li key={v.ncessch}>
            <Link
              href={`/schools/${v.ncessch}`}
              className="group flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-accent-soft/50"
            >
              <div className="min-w-0">
                <div className="truncate font-medium group-hover:text-accent">{v.name}</div>
                <div className="truncate text-sm text-muted">{[v.place, v.operator].filter(Boolean).join(" · ")}</div>
              </div>
              <div className="shrink-0 text-right">
                {v.status && v.status in STATUS_CLASS && (
                  <span className={`rounded-sm px-1.5 py-0.5 text-xs font-medium ${STATUS_CLASS[v.status]}`}>
                    {STATUS_LABEL[v.status]}
                  </span>
                )}
                <div className="mt-0.5 text-xs text-muted">{ago(v.at, now)}</div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
      <p className="border-t border-line px-4 py-2 text-xs text-muted">Kept in this browser only.</p>
    </section>
  );
}
