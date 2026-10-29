"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

// The header links, with the current section highlighted. A school's page
// counts as Search, since that's where it was found.
function Links({ lists }: { lists?: boolean }) {
  const link = (active: boolean) =>
    `rounded-sm px-2 py-1 whitespace-nowrap sm:px-2.5 ${active ? "bg-accent-soft text-accent" : "text-muted hover:text-ink"}`;
  return (
    <nav className="flex gap-1 text-sm">
      <Link href="/" className={link(lists === false)}>
        Search
      </Link>
      <Link href="/schools" className={link(lists === true)}>
        <span className="sm:hidden">Lists</span>
        <span className="hidden sm:inline">School lists</span>
      </Link>
    </nav>
  );
}

function CurrentLinks() {
  return <Links lists={usePathname() === "/schools"} />;
}

// The path is only known at request time, so the links render unhighlighted
// first and the highlight streams in, rather than holding up the whole page.
export default function SiteNav() {
  return (
    <Suspense fallback={<Links />}>
      <CurrentLinks />
    </Suspense>
  );
}
