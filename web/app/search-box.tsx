"use client";

import { useRouter } from "next/navigation";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { flushSync } from "react-dom";
import type { OrgSuggestion, SchoolSuggestion } from "@/lib/schools";

type Item = { kind: "school"; school: SchoolSuggestion } | { kind: "org"; org: OrgSuggestion };

const GROUPS = [
  { kind: "school", label: "Schools" },
  { kind: "org", label: "Nonprofits and management companies" },
] as const;

// The home search box, suggesting schools and the organizations behind them as
// you type. It stays an input named q inside the page's form, so Enter with
// nothing highlighted, or no JavaScript at all, still runs the full search.
// Picking a school opens its page; picking an organization searches its schools.
export default function SearchBox({
  defaultValue,
  placeholder,
  className,
}: {
  defaultValue: string;
  placeholder: string;
  className: string;
}) {
  const router = useRouter();
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const request = useRef<AbortController>(undefined);
  const seen = useRef(new Map<string, Item[]>());
  const [value, setValue] = useState(defaultValue);
  const [items, setItems] = useState<Item[]>([]);
  const [open, setOpen] = useState(false);
  // The highlighted suggestion; -1 is the box itself.
  const [active, setActive] = useState(-1);
  const shown = open && value.trim().length >= 2 && items.length > 0;
  const optionId = (i: number) => `${id}-option-${i}`;

  function show(found: Item[]) {
    setItems(found);
    setActive(-1);
    setOpen(true);
  }

  // Ask once typing pauses, and drop an answer the next keystroke has overtaken.
  function lookup(text: string) {
    clearTimeout(timer.current);
    request.current?.abort();
    const st = (input.current?.form?.elements.namedItem("st") as HTMLSelectElement | null)?.value ?? "";
    // Matching ignores case, so one cached answer serves "kipp" and "KIPP".
    const q = text.trim().toLowerCase();
    if (q.length < 2) return;
    const key = new URLSearchParams({ q, st }).toString();
    const known = seen.current.get(key);
    if (known) return show(known);
    timer.current = setTimeout(async () => {
      const controller = (request.current = new AbortController());
      try {
        const res = await fetch(`/api/suggest?${key}`, { signal: controller.signal });
        if (!res.ok) return;
        const body: { schools: SchoolSuggestion[]; orgs: OrgSuggestion[] } = await res.json();
        const found: Item[] = [
          ...body.schools.map((school) => ({ kind: "school" as const, school })),
          ...body.orgs.map((org) => ({ kind: "org" as const, org })),
        ];
        seen.current.set(key, found);
        show(found);
      } catch {
        // Overtaken by the next keystroke, or offline: keep the last list.
      }
    }, 150);
  }

  function choose(item: Item) {
    setOpen(false);
    if (item.kind === "school") {
      router.push(`/schools/${item.school.ncessch}`);
      return;
    }
    // Put the name in the box first, then search as if it had been typed.
    flushSync(() => setValue(item.org.name));
    input.current?.form?.requestSubmit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && items.length && value.trim().length >= 2) {
      e.preventDefault();
      // Step through the suggestions and back to the box, wrapping at both ends.
      const slots = items.length + 1;
      const next = ((active + 1 + (e.key === "ArrowDown" ? 1 : -1) + slots) % slots) - 1;
      setOpen(true);
      setActive(next);
      if (next >= 0) document.getElementById(optionId(next))?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && shown && active >= 0) {
      e.preventDefault();
      choose(items[active]);
    } else if (e.key === "Escape" && shown) {
      e.preventDefault();
      setOpen(false);
      setActive(-1);
    }
  }

  return (
    // A row of its own on a phone, so the suggestions have room to be read.
    <div className="relative w-full min-w-0 sm:w-auto sm:flex-1">
      <input
        ref={input}
        name="q"
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          setValue(e.target.value);
          lookup(e.target.value);
        }}
        // Refocusing picks up a state chosen in the meantime.
        onFocus={() => lookup(value)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={shown}
        aria-controls={`${id}-list`}
        aria-activedescendant={shown && active >= 0 ? optionId(active) : undefined}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        className={`w-full ${className}`}
      />
      <div
        id={`${id}-list`}
        role="listbox"
        aria-label="Suggestions"
        hidden={!shown}
        className="absolute inset-x-0 top-full z-10 mt-1 max-h-[min(28rem,60vh)] overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-lg"
      >
        {GROUPS.map((group) => {
          const members = items.map((item, i) => ({ item, i })).filter(({ item }) => item.kind === group.kind);
          if (!members.length) return null;
          return (
            <div key={group.kind} role="group" aria-labelledby={`${id}-${group.kind}`}>
              <div id={`${id}-${group.kind}`} role="presentation" className="px-3 pt-2 pb-1 text-xs text-muted">
                {group.label}
              </div>
              {members.map(({ item, i }) => (
                <div
                  key={i}
                  id={optionId(i)}
                  role="option"
                  aria-selected={i === active}
                  // Keep focus in the box, so the list is still there when the click lands.
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(item)}
                  className={`flex cursor-pointer items-baseline justify-between gap-3 px-3 py-1.5 ${
                    i === active ? "bg-accent-soft" : ""
                  }`}
                >
                  {item.kind === "school" ? (
                    <>
                      <span className="truncate">{item.school.sch_name}</span>
                      <span className="shrink-0 text-sm text-muted">
                        {[`${item.school.lcity}, ${item.school.st}`, !item.school.is_operating && "not operating"]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="truncate">{item.org.name}</span>
                      <span className="shrink-0 text-sm text-muted">
                        {item.org.schools.toLocaleString("en-US")} {item.org.schools === 1 ? "school" : "schools"}
                      </span>
                    </>
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
