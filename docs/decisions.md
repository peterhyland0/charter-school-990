# Design decisions

What was decided, why, and what it costs. Numbers are from the current national run: 8,129 operating charter schools in 47 states. 3,453 are verified, 1,929 likely and 2,747 unmatched.

## Data

- **Every return is keyed on the IRS OBJECT_ID.** A nonprofit can file an original and an amended return for the same year, so "the latest" is the newest tax period, then the newest filing time.
- **Each return is found by reading the zips' tables of contents, not the index's batch label.** The label is wrong for May: returns labelled 05A sit in both the 05A and 05B zips. *Cost:* one table-of-contents read per zip per run.
- **Single returns are read out of the IRS zips with HTTP range requests.** That fetches about 27,000 returns instead of downloading the full archive. The large May zips use Deflate64, which Python can't decompress, so those returns are decompressed separately. *Cost:* reliance on an IRS host that rate-limits. Downloads fall back to the last cached copy, and the fetch stops rather than retrying when the host refuses.
- **Both the 2025 and 2026 indexes are read,** because a nonprofit's newest return can sit in either one.
- **Candidate nonprofits come from the IRS master file by two routes:**
  - **IRS school codes:** coded as a school, education category B29, or "charter" in the name.
  - **Network name:** a name that matches a charter school's or district's name, such as KIPP or Great Hearts. Several big networks aren't coded as schools, so codes alone miss them.

  Boosters, PTAs and alumni groups are dropped. The result is 57,024 nonprofits and 27,003 parsed returns.

## Matching

- **Matching is SQL in Postgres, using trigram similarity.** The rules stay readable, run against the real data, and match the whole country in about 15 seconds.
- **Six signals:** website, name + ZIP, name + city, charter-district name, phone, and verified sibling schools in the same charter district. Website alone misses schools with no site and over-links shared network sites. Name alone fails on generic names.
- **Tiers.** "Verified" means two independent signals agree; "likely" means one does. The two district signals count as one, so no school is verified on district membership alone.
- **Names are compared on their distinctive words.** Generic words like CHARTER and SCHOOL are dropped. Numbers must agree (ICAHN 1 is not ICAHN 4), and a name that reduces to just a city or state counts for nothing.
- **Exclusions:**
  - Website builders and social media sites.
  - Domains on more than 10 nonprofits' returns, which are umbrella sites.
  - Regular school districts' own websites. A Baltimore district domain had linked 30 unrelated schools.
  - Six statewide authorizer districts (NV, CO, TN and three in SC), whose schools are unrelated to each other.
- **Roles among nonprofits tied for a school, applied in order:**
  1. Out of state → affiliate.
  2. Foundation, facilities or lender → support.
  3. Where some delegate management (Form 990 Part VI) and some don't, the ones they delegate to → management organization.
  4. Direct evidence beats a district-name match.
  5. A clearly closer name wins.

  Whatever is left runs the school. If that's more than one nonprofit, the school is shown as ambiguous (285 schools) rather than guessed. *Check:* the first spot check of 20 resolved ties found 13 right. Three error patterns were fixed, and a fresh 20 were all right.
- **Every unmatched school gets a reason.** That includes the IRS filing code for a nonprofit that matches but has no 990 on file: a 990-N postcard filer, a government body, or no recent return.

## Corrections

- **Hand fixes are stored in Postgres and applied last on every run,** so they always win and survive reruns. Each school has at most one active answer, and each fix carries a note and who made it. There is deliberately no foreign key to the schools table, because that table is reloaded on every run. A Dagster check fails if a fix stops applying.

## Stack

- **Python with Dagster assets, Postgres, and Next.js with TypeScript,** as in the job description. Neon and Vercel host it. Dagster provides reruns and 9 checks that fail or warn loudly. A rerun from cache takes about a minute, and it fetches only returns it hasn't seen, so a monthly rerun keeps the data current. The schedule that triggers it is not wired up yet.

## Known limits

- **Returns lag.** Most cover a year ending June 2025.
- **Low match rates in three states:** Michigan 13%, Wisconsin 23% and New Mexico 19%. My unverified guess is that many charters there are public bodies or run by for-profit managers.
- **Networks where each campus has its own website and the parent files elsewhere** are missed, for example Mater Academy in Florida.
- **Ambiguous schools,** such as KIPP NYC and Great Hearts, need a correction or more evidence, such as the related-organizations schedule (Schedule R) and the contractor list.
- **Accuracy is measured by spot checks only.** No full labelled sample exists yet.
