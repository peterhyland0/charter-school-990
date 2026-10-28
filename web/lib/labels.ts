export type Status = "verified" | "likely" | "unmatched";

export const STATUS_LABEL: Record<Status, string> = {
  verified: "Verified",
  likely: "Likely",
  unmatched: "Unmatched",
};

export const STATUS_CLASS: Record<Status, string> = {
  verified: "bg-emerald-600/15 text-emerald-700 dark:text-emerald-300",
  likely: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  unmatched: "bg-black/5 text-black/60 dark:bg-white/10 dark:text-white/60",
};

export const SIGNAL_LABEL: Record<string, string> = {
  website: "Website",
  name_zip: "Name + ZIP",
  name_city: "Name + city",
  district_name: "Charter district name",
  phone: "Phone",
};

type Detail = { has_website?: boolean; postcard_ein?: string; postcard_name?: string };

export function reasonText(reason: string | null, detail: Detail): string {
  switch (reason) {
    case "district_governed_state":
      return "Charter schools in this state are governed by school districts, so there is usually no separate nonprofit to find.";
    case "filer_without_efile":
      return `A nonprofit with a matching name and ZIP, ${detail.postcard_name ?? "unnamed"}, is registered with the IRS but filed no Form 990 or 990-EZ in 2025 or 2026. It most likely files only the 990-N postcard, which has no financial detail.`;
    case "inside_regular_district":
      return "This school is listed under a regular school district rather than its own charter district, and no nonprofit matched it. It may be run by the district.";
    default:
      return detail.has_website === false
        ? "No nonprofit matched by name, phone or district name, and the NCES directory lists no website to match on."
        : "No nonprofit matched by website, name, phone or district name.";
  }
}
