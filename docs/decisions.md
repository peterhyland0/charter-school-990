# Design decisions

What was decided, why, and what it costs. Numbers are from the current national run: 8,129 operating charter schools in 44 states, DC, Puerto Rico and Guam. 4,091 are verified, 1,647 likely and 2,391 unmatched. Before outside evidence was added (below), it was 3,484, 1,912 and 2,733.

## Data

- **Returns are keyed on the IRS OBJECT_ID**, so an amended return doesn't overwrite its original; "latest" is the newest tax period, then the newest filing.
- **Each return is located by reading the zips' contents, not the index's batch label,** which is wrong for May (05A returns sit in two zips). Single returns are read with HTTP range requests, about 27,000 instead of the full archive, including May's Deflate64 zips. The 2025 and 2026 indexes are both read.
- **Candidate nonprofits come from the IRS master file by three routes:**
  - **IRS school codes.**
  - **A network name matching a charter school or district,** such as KIPP or Great Hearts. Big networks often aren't coded as schools.
  - **Named as a school's holder outside the IRS data** (see "Outside evidence"). This added 131 nonprofits the first two routes missed.

  Support groups like boosters and PTAs are left out, because they share a school's name but don't run it. The result is 57,155 nonprofits and 27,125 parsed returns.
- **The IRS changes the return format most years** (11 schema versions in the current data). The parser tries the current element name first, then older ones. A renamed field wouldn't raise an error, only come back empty, so two checks watch for it:
  - parse failures above 1%;
  - a schema version where a field used for matching (name, address, phone, website, the delegation answer) is empty 50 points more often than on earlier versions. Versions already differ by up to 35 points.

## Matching

- **Matching is SQL in Postgres, using trigram similarity.** The rules stay readable, run on the real data, and match the country in about 15 seconds.
- **Nine signals:** website, name + ZIP, name + city, charter-district name, phone, verified siblings in the same charter district, a network-site name pick, a federal audit and a state charter-holder record. The name pick is for one website shared by many campus nonprofits, such as Summit Academies' 21. It links the in-state nonprofit whose name clearly matches the school's, and agreed with existing matches 192 times in 196.
- **Tiers.** "Verified" means two independent signals agree; "likely" means one does. The two district signals count once, so district membership alone never verifies a school. A federal audit counts once with the name signals, because it is also a name-and-place match.
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
  4. Direct evidence beats a district-name match or a federal audit alone. Without this rule the Orlando Science Center, a museum, took Orlando Science Elementary from the nonprofit on the school's website.
  5. A clearly closer name wins.

  What remains runs the school. If that's more than one, the school is shown as ambiguous (280 schools) rather than guessed. *Check:* a first spot check of 20 found 13 right. After fixing three error patterns, a fresh 20 were all right.
- **Every unmatched school gets a reason.** The most specific evidence wins:
  - **The IRS filing code,** for a matching nonprofit with no 990: a 990-N filer, a government body, or no recent return.
  - **The school lists its regular district's website.**
  - **A state where charters are district-run or government bodies by law.** Michigan's public school academies are governmental agencies (MCL 380.501).
  - **The school's federal audit is filed by a city, county or school district** that files no 990 (10 schools).

## Outside evidence

A 990 never says which charter schools its filer holds, so a school stays unmatched when its holder's name, address and website all differ from the school's. Four public sources name the holder. Each is stored per school in `external_links` and refreshed monthly, and a source that can't be fetched keeps its previous rows.

- **Federal Audit Clearinghouse.** A charter that spends $750k or more of federal money in a year files a single audit naming its EIN and address. An auditee in the school's state is linked when its name matches the school's at the same ZIP or city, or it sits at the same street address. *Check:* on schools already matched, the auditee is the matched operator 87% of the time; most of the rest are a network's filer against a campus's.
- **Texas Education Agency's charter-holder report** (995 schools) and **Louisiana's charter list** (128): the legal holder of each charter, matched to filers by name.
- **Arizona's** charter districts are their holders, and NCES already carries the name, with the state's entity ID appended ("Center for Academic Success Inc. (4191)"). It now matches once the ID is removed.
- **Florida and California publish no holder names,** so most of their unmatched schools remain.

*Result:* unmatched fell from 2,733 to 2,391 and verified rose from 3,484 to 4,091. Against the previous live matches, 342 schools gained a link, none lost one, and 68 changed operator, mostly to the state's holder of record (Pioneer Technology → Meadow Oaks Education Foundation). On the 25-school eval set the matcher went from 17 to 18 right, with no new wrong answers.

## Management companies and finances

- **"Does a management company decide?" is answered from four places on the returns, shown side by side:**
  - the Part VI line 3 yes/no answer;
  - any contractor in Part VII-B whose service is running schools;
  - the Schedule O text explaining line 3;
  - a related organization on Schedule R that manages the school or provides its services (below).

  Of returns that say yes, 96% name the manager in at least one place. Mater Academy's names Academica Dade LLC, an "educational service provider" paid $10.5M. A contractor counts by its service description; property, food, construction and investment management don't.
- **Schedule R links a school's nonprofit to its network and management organization.** 4,631 returns list related organizations by EIN, with each one's activity and the money between them. A related organization is:
  - **its management organization** when it provides services (Part V) or the return calls it a management company, and it is described as school-related or the return says management is delegated. That gives 128 links covering 323 schools; for 263 of them no other source named a manager. Examples: Summit Academy Management, Imagine Schools, Lifelong Learning Administration.
  - **in its network** when it runs or is described as a school, or shares paid staff: 2,158 links covering 1,033 schools.

  Authorizing districts, universities, foundations, facilities and lending companies, and unrelated businesses (a hospital, a fund) are left out. The "controlled entity" box isn't used, because in 426 pairs of school nonprofits each claims to control the other. For-profit managers such as Academica never appear, because they aren't related by control.
- **Finances carry their line numbers, and each school says when the next return is due,** so June 2025 figures aren't presented as current.
- **No public "suggest a match" button yet.** The site reads through a role that can't write. A suggestion box needs its own insert-only table, spam protection and a review step, so corrections stay with `make correct` for now.

## Lists and exports

- **One query serves the list page, the CSV and the JSON,** one row per school, so they can't disagree. Search also matches the nonprofit and management company behind a school ("Academica" finds 68).
- **The CSV is spreadsheet-safe:** formula-like cells are guarded, and Excel reads accents. "Top-paid leader" skips unpaid board members, who are often the only people listed when a management company employs the staff.

## Corrections

- **Hand fixes are stored in Postgres and applied last on every run,** so they win and survive reruns. One active answer per school, each with a note and its author; a Dagster check fails if a fix stops applying.

## Stack

- **Python and Dagster, Postgres, and Next.js with TypeScript,** as in the job description, hosted on Neon and Vercel. Dagster provides reruns, 13 loud checks and a monthly schedule. A rerun fetches only returns it hasn't seen.
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
- **Ambiguous schools** (KIPP NYC, Great Hearts) need a correction or more evidence. Schedule R is shown on each school but doesn't yet decide between tied operators.
- **Accuracy is measured by spot checks only.**
