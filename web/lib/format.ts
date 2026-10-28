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
