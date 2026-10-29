"use client";

import { useState, type ReactNode } from "react";

// The rows of a long table past the first few, behind a button, so a return
// that lists 40 people doesn't push the rest of the page out of reach. The
// rows are rendered on the server and passed in; this only shows or hides them.
export default function MoreRows({
  hidden,
  columns,
  noun,
  children,
}: {
  hidden: number;
  columns: number;
  noun: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {open && children}
      <tr>
        <td colSpan={columns} className="pt-2">
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="text-sm text-accent-strong hover:underline"
          >
            {open ? "Show fewer" : `Show ${hidden} more ${noun}`}
          </button>
        </td>
      </tr>
    </>
  );
}
