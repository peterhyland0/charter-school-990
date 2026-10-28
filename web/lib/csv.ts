// CSV that opens cleanly in Excel and Numbers: RFC 4180 quoting, a UTF-8
// byte-order mark so Excel reads accents, and a guard against cells that a
// spreadsheet would run as a formula (a value starting with =, +, - or @).
type Cell = string | number | boolean | null | undefined;

function cell(value: Cell): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(header: string[], rows: Cell[][]): string {
  return "﻿" + [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}
