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
  district_group: "Same charter district",
  network_site: "Network website and name",
  audit: "Federal single audit",
  holder: "State charter-holder record",
  adjudicated: "Adjudicated",
};

export type Role = "operator" | "management" | "support" | "affiliate" | "related" | "other";

export const ROLE_LABEL: Record<Role, string> = {
  operator: "Runs this school",
  management: "Management organization",
  support: "Network support entity (foundation, facilities or lender)",
  affiliate: "Same network, another state",
  related: "Related, less likely to run this school",
  other: "Weaker match",
};

type Detail = {
  has_website?: boolean;
  nonprofit_ein?: string;
  nonprofit_name?: string;
  filing_req_cd?: string;
  correction_note?: string;
  state_law?: string;
  auditee_name?: string;
  auditee_ein?: string;
};

export function reasonText(reason: string | null, detail: Detail): string {
  const nonprofit = detail.nonprofit_name ?? "a nonprofit";
  switch (reason) {
    case "corrected_none":
      return `Set by hand: this school has no nonprofit of its own. Note: ${detail.correction_note ?? "none"}`;
    case "district_governed_state":
      return "Charter schools in this state are governed by school districts, so there is usually no separate nonprofit to find.";
    case "postcard_filer":
      return `A nonprofit with a matching name and ZIP, ${nonprofit}, is registered with the IRS as a small organization that files only the 990-N postcard, which has no financial detail.`;
    case "not_required_to_file":
      return `A nonprofit with a matching name and ZIP, ${nonprofit}, is registered with the IRS but isn't required to file a Form 990, for example because it is a government body.`;
    case "no_recent_return":
      return `A nonprofit with a matching name and ZIP, ${nonprofit}, normally files a Form 990, but none was published in 2025 or 2026. Its return may be late or not yet released.`;
    case "public_body_state":
      return `Charter schools in this state are government bodies under state law (${detail.state_law ?? "state statute"}), not nonprofits, so most have no Form 990 to find.`;
    case "government_auditee":
      return `This school's federal single audit is filed by ${detail.auditee_name ?? "a government body"}, a city, county or school district that files no Form 990, so it is probably run by that government rather than a nonprofit.`;
    case "uses_district_website":
      return "This school lists its school district's website, so it is probably run by the district and has no separate nonprofit.";
    case "inside_regular_district":
      return "This school is listed under a regular school district rather than its own charter district, and no nonprofit matched it. It may be run by the district.";
    default:
      return detail.has_website === false
        ? "No nonprofit matched by name, phone or district name, and the NCES directory lists no website to match on."
        : "No nonprofit matched by website, name, phone or district name.";
  }
}
