// postgres.js returns bigint and numeric columns as strings.
export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

export function day(value: Date | string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function propublicaUrl(ein: string, objectId: string): string {
  return `https://projects.propublica.org/nonprofits/organizations/${ein.padStart(9, "0")}/${objectId}/full`;
}

export function formatEin(ein: string): string {
  const e = ein.padStart(9, "0");
  return `${e.slice(0, 2)}-${e.slice(2)}`;
}

// A Form 990 is due on the 15th day of the 5th month after the year ends, and
// the usual extension adds 6 months. So a year ending June 30 is due Nov 15,
// or May 15 with the extension.
export function nextReturn(periodEnd: Date | string | null | undefined):
  | { yearEnd: Date; due: Date; extended: Date }
  | null {
  if (!periodEnd) return null;
  const end = new Date(periodEnd);
  const yearEnd = new Date(Date.UTC(end.getUTCFullYear() + 1, end.getUTCMonth(), end.getUTCDate()));
  const due = new Date(Date.UTC(yearEnd.getUTCFullYear(), yearEnd.getUTCMonth() + 5, 15));
  const extended = new Date(Date.UTC(due.getUTCFullYear(), due.getUTCMonth() + 6, 15));
  return { yearEnd, due, extended };
}

export function month(value: Date | string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-US", { timeZone: "UTC", year: "numeric", month: "short" });
}
