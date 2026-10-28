# Design decisions

What was decided, why, and what it costs. Numbers are from the current national run: 8,129 operating charter schools in 47 states. 3,484 are verified, 1,912 likely and 2,733 unmatched.

## Data

- **Every return is keyed on the IRS OBJECT_ID.** A nonprofit can file an original and an amended return for one year, so "latest" means the newest tax period, then the newest filing time.
- **Each return is found by reading the zips' tables of contents, not the index's batch label.** May's label is wrong: returns labelled 05A sit in both the 05A and 05B zips.
- **Single returns are read out of the IRS zips with HTTP range requests.** That fetches about 27,000 returns instead of downloading the full archive. May's zips use Deflate64, which Python can't decompress, so those are decompressed separately. *Cost:* reliance on an IRS host that rate-limits. Downloads fall back to the last cached copy.
- **Both the 2025 and 2026 indexes are read,** because a nonprofit's newest return can be in either.
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

## Corrections

- **Hand fixes are stored in Postgres and applied last on every run,** so they win and survive reruns.
  - Each school has at most one active answer, and each fix records a note and who made it.
  - There is deliberately no foreign key to `schools`, which is reloaded every run.
  - A Dagster check fails if a fix stops applying.

## Stack

- **Python and Dagster, Postgres, and Next.js with TypeScript,** as in the job description, hosted on Neon and Vercel. Dagster provides reruns, 9 loud checks and a monthly schedule. A rerun fetches only returns it hasn't seen.
- **Live layout:**
  - **Neon and Vercel** are both in US East, for fast pages.
  - **The monthly Dagster job** is packaged for a small EU server (`deploy/`), which isn't running yet. That costs a fraction of a US server, and its SQL runs inside Neon either way.
  - **A run never empties the live site:** schools are synced in place and matches are replaced in one transaction.
  - **The website** reads as a read-only role.
  - **The Dagster UI** has no login, so it is reachable only over SSH.

## Known limits

- **Returns lag:** most cover a year ending June 2025.
- **Low matching in Wisconsin (23%) and New Mexico (19%) is unexplained.** In Wisconsin, each district decides whether a charter is part of the district (Wis. Stat. 118.40(7)).
- **Missed networks:** ones where each campus has its own website and the parent files elsewhere, such as Mater Academy in Florida.
- **Ambiguous schools** (KIPP NYC, Great Hearts) need a correction or more evidence, such as Schedule R and the contractor list.
- **Accuracy is measured by spot checks only.**
