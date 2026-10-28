# Eval set

A small labelled sample used to measure how often the matcher, and the models that resolve hard cases, name the right nonprofit and management company.

## How it was built

- **Sample.** 25 operating charter schools, 5 from each group the system produces:
  - verified;
  - likely;
  - ambiguous (several nonprofits tie);
  - unmatched;
  - schools with a named management company.

  Each group was drawn at random with a fixed seed: schools ordered by `md5(ncessch || 'label-2026-10-28')`, first 5 per group, no school in two groups. Rerunning the draw gives the same schools.
- **Labels were researched, not hand-entered.** Under the time constraint, each school was researched on the web by an AI research agent, working blind: it saw only the school's directory entry (name, address, website, district, authorizer), never the system's answer or which group the school came from. The sources were used in this order:
  1. state and authorizer charter lists;
  2. the school's own site and audits;
  3. ProPublica or IRS records, to confirm the legal name and EIN.

  Every label cites at least one source, and I opened each cited source before accepting the label.
- **What a label says:**
  - the nonprofit that holds the charter and runs the school, by EIN, or that the school has none (district-run, public body, for-profit);
  - its management company, or none;
  - a confidence level and the evidence URLs.

## Limits

- **AI-researched labels can be wrong**, even with sources. Scores are reported with and without low-confidence labels.
- **25 schools, 5 per group, is small.** Read results as counts, not precise rates.
- **Hand labelling is the follow-up:** a person verifying each label, then growing the set to about 50 per group. The 40-school blind sheet for that is ready.

## Use

```bash
make eval    # scores every method against labels-*.csv, writes results.md
```
