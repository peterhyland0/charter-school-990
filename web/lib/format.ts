// postgres.js returns bigint and numeric columns as strings.
export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

// Dates are shown in UTC: return dates are calendar dates without a time zone.
function formatDate(value: Date | string | null | undefined, options: Intl.DateTimeFormatOptions): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-US", { timeZone: "UTC", ...options });
}

export function day(value: Date | string | null | undefined): string {
  return formatDate(value, { year: "numeric", month: "short", day: "numeric" });
}

export function month(value: Date | string | null | undefined): string {
  return formatDate(value, { year: "numeric", month: "short" });
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
  const year = end.getUTCFullYear() + 1;
  // Same day a year later, clamped to the month's length (Feb 29 -> Feb 28).
  const lastDay = new Date(Date.UTC(year, end.getUTCMonth() + 1, 0)).getUTCDate();
  const yearEnd = new Date(Date.UTC(year, end.getUTCMonth(), Math.min(end.getUTCDate(), lastDay)));
  const due = new Date(Date.UTC(year, end.getUTCMonth() + 5, 15));
  const extended = new Date(Date.UTC(year, end.getUTCMonth() + 11, 15));
  return { yearEnd, due, extended };
}
