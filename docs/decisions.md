# Design decisions

What was decided, why, and what it costs. Numbers are from the current national run: 8,129 operating charter schools in 47 states. 3,484 are verified, 1,912 likely and 2,733 unmatched.

## Data

- **Returns are keyed on the IRS OBJECT_ID**, so an amended return doesn't overwrite its original; "latest" is the newest tax period, then the newest filing.
- **Each return is located by reading the zips' contents, not the index's batch label,** which is wrong for May (05A returns sit in two zips). Single returns are read with HTTP range requests, about 27,000 instead of the full archive, including May's Deflate64 zips. The 2025 and 2026 indexes are both read.
- **Candidate nonprofits come from the IRS master file by two routes:**
  - **IRS school codes.**
  - **A network name matching a charter school or district,** such as KIPP or Great Hearts. Big networks often aren't coded as schools.

  Boosters, PTAs and alumni groups are dropped. The result is 57,024 nonprofits and 27,003 parsed returns.

## Matching

- **Matching is SQL in Postgres, using trigram similarity.** The rules stay readable, run on the real data, and match the country in about 15 seconds.
- **Seven signals:** website, name + ZIP, name + city, charter-district name, phone, verified siblings in the same charter district, and a network-site name pick. The name pick is for one website shared by many campus nonprofits, such as Summit Academies' 21. It links the in-state nonprofit whose name clearly matches the school's, and agreed with existing matches 192 times in 196.
- **Tiers.** "Verified" means two independent signals agree; "likely" means one does. The two district signals count once, so district membership alone never verifies a school.
- **Names are compared on their distinctive words.** CHARTER, SCHOOL and the like are dropped. Numbers must agree (ICAHN 1 is not ICAHN 4), and a name that is only a city or state counts for nothing.
- **Exclusions:**
  - Site builders and social media.
  - Domains on more than 10 nonprofits' returns.
  - Regular districts' own websites. A Baltimore district domain had linked 30 unrelated schools.
  - Six statewide authorizer districts (NV, CO, TN and three in SC).
- **Roles among tied nonprofits, applied in order:**
  1. Out of state → affiliate.
  2. Foundation, facilities or lender → support.
  3. Where some delegate management (Form 990 Part VI) and some don't, the ones they delegate to → management organization.
  4. Direct evidence beats a district-name match.
  5. A clearly closer name wins.

  What remains runs the school. If that's more than one, the school is shown as ambiguous (285 schools) rather than guessed. *Check:* a first spot check of 20 found 13 right. After fixing three error patterns, a fresh 20 were all right.
- **Every unmatched school gets a reason.** The most specific evidence wins:
  - **The IRS filing code,** for a matching nonprofit with no 990: a 990-N filer, a government body, or no recent return.
  - **The school lists its regular district's website.**
  - **A state where charters are district-run or government bodies by law.** Michigan's public school academies are governmental agencies (MCL 380.501).

## Management companies and finances

- **"Does a management company decide?" is answered from three places on the return, shown side by side:**
  - the Part VI line 3 yes/no answer;
  - any contractor in Part VII-B whose service is running schools;
  - the Schedule O text explaining line 3.

  Of returns that say yes, 96% name the manager in at least one place. Mater Academy's names Academica Dade LLC, an "educational service provider" paid $10.5M. A contractor counts by its service description; property, food, construction and investment management don't.
- **Finances carry their line numbers, and each school says when the next return is due,** so June 2025 figures aren't presented as current.
- **No public "suggest a match" button yet.** The site reads through a role that can't write. A suggestion box needs its own insert-only table, spam protection and a review step, so corrections stay with `make correct` for now.

## Corrections

- **Hand fixes are stored in Postgres and applied last on every run,** so they win and survive reruns. One active answer per school, each with a note and its author; a Dagster check fails if a fix stops applying.

## Stack

- **Python and Dagster, Postgres, and Next.js with TypeScript,** as in the job description, hosted on Neon and Vercel. Dagster provides reruns, 9 loud checks and a monthly schedule. A rerun fetches only returns it hasn't seen.
- **Live layout:**
  - **Neon and Vercel** are both in US East, for fast pages.
  - **The monthly Dagster job is ready to deploy** to a small EU server (`deploy/setup.sh`, systemd units, the schedule switched on by one setting). That costs a fraction of a US server, and its SQL runs inside Neon either way.
  - **A run never empties the live site:** schools are synced in place and matches are replaced in one transaction.
  - **The website** reads as a read-only role.
  - **The Dagster UI** has no login, so it is reachable only over SSH.

## Known limits

- **Returns lag:** most cover a year ending June 2025.
- **Low matching in Wisconsin (23%) and New Mexico (19%) is unexplained.** In Wisconsin, each district decides whether a charter is part of the district (Wis. Stat. 118.40(7)).
- **Missed networks:** ones where each campus has its own website and the parent files elsewhere. Most Mater Academy campuses in Florida are unmatched, though matched ones now show Academica as manager.
- **Ambiguous schools** (KIPP NYC, Great Hearts) need a correction or more evidence, such as Schedule R and the contractor list.
- **Accuracy is measured by spot checks only.**
