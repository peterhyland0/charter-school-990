"""Score the matcher, and any adjudicator, against the researched eval set.

    python -m charter990.evaluate            # print the scores, write eval/results.md

Each label says which nonprofit runs a school (EIN), or that it has none, and
names its management company. A method's answer for a school is:
- the matcher: its single operator, none if unmatched, or "ambiguous" when
  several nonprofits tie;
- an adjudicator (the scored model or the LLM): its decision where it made one
  (ambiguous and unmatched schools), otherwise the matcher's answer.
"""

import csv
import os
from collections import defaultdict
from pathlib import Path

import psycopg

from .settings import REPO_ROOT
from .util import name_key, named_in

EVAL_DIR = REPO_ROOT / "eval"
GROUPS = ["verified", "likely", "ambiguous", "unmatched", "managed"]


def latest_labels() -> Path:
    files = sorted(EVAL_DIR.glob("labels-*.csv"))
    if not files:
        raise SystemExit(f"no eval/labels-*.csv in {EVAL_DIR}")
    return files[-1]


def load_labels(path: Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        r["operator_ein"] = (r.get("operator_ein") or "").replace("-", "").strip().zfill(9) if r.get("operator_ein") else ""
        r["no_nonprofit"] = (r.get("no_nonprofit") or "").strip().lower() in ("true", "yes", "1")
        rows.append(r)
    return rows


def system_answers(conn, ids: list[str]) -> dict[str, dict]:
    """The matcher's operators, every linked nonprofit, and its management company."""
    answers = {i: {"operators": [], "linked": set(), "managers": [], "status": None} for i in ids}
    for ncessch, ein, role in conn.execute(
        "SELECT ncessch, ein, role FROM school_matches WHERE ncessch = ANY(%s)", (ids,)
    ).fetchall():
        answers[ncessch]["linked"].add(ein)
        if role == "operator":
            answers[ncessch]["operators"].append(ein)
    for ncessch, status in conn.execute(
        "SELECT ncessch, status FROM school_status WHERE ncessch = ANY(%s)", (ids,)
    ).fetchall():
        answers[ncessch]["status"] = status
    for ncessch, name in conn.execute(
        """SELECT m.ncessch, c.name FROM school_matches m JOIN filing_contractors c ON c.object_id = m.object_id
           WHERE m.ncessch = ANY(%s) AND m.role = 'operator' AND c.is_management
           UNION
           SELECT m.ncessch, f.org_name FROM school_matches m JOIN filings f ON f.object_id = m.object_id
           WHERE m.ncessch = ANY(%s) AND m.role = 'management'""",
        (ids, ids),
    ).fetchall():
        answers[ncessch]["managers"].append(name)
    return answers


def adjudications(conn, ids: list[str]) -> dict[str, dict[str, str | None]]:
    """The latest decision per school and method, if the adjudications table exists."""
    (exists,) = conn.execute("SELECT to_regclass('public.adjudications') IS NOT NULL").fetchone()
    if not exists:
        return {}
    decided: dict[str, dict[str, str | None]] = defaultdict(dict)
    for ncessch, method, chosen in conn.execute(
        """SELECT DISTINCT ON (ncessch, method) ncessch, method, chosen_ein
           FROM adjudications WHERE ncessch = ANY(%s) ORDER BY ncessch, method, decided_at DESC""",
        (ids,),
    ).fetchall():
        decided[method][ncessch] = chosen
    return decided


def same_org(a: str | None, b: str | None) -> bool:
    """Management company names agree, allowing for LLC/Inc and extra words."""
    if not a or not b:
        return False
    ka, kb = name_key(a), name_key(b)
    return bool(ka and kb and (ka == kb or named_in(a, b) or named_in(b, a)))


def operator_outcome(label: dict, answer: str | None, ambiguous: bool) -> str:
    """right, wrong, missed (said none, but there is one), false_link, ambiguous, or unlabelled."""
    if label["no_nonprofit"]:
        return "right" if answer is None and not ambiguous else "false_link"
    if not label["operator_ein"]:
        return "unlabelled"
    if ambiguous:
        return "ambiguous"
    if answer is None:
        return "missed"
    return "right" if answer == label["operator_ein"] else "wrong"


def manager_outcome(label: dict, managers: list[str]) -> str:
    truth = (label.get("management_company") or "").strip()
    says_none = truth.lower() in ("", "none", "null")
    if says_none:
        return "right" if not managers else "wrong"
    return "right" if any(same_org(truth, m) for m in managers) else "wrong"


def evaluate(dsn: str) -> str:
    path = latest_labels()
    labels = load_labels(path)
    ids = [r["ncessch"] for r in labels]
    with psycopg.connect(dsn) as conn:
        system = system_answers(conn, ids)
        decided = adjudications(conn, ids)

    methods = ["matcher", *sorted(decided)]
    # tally[method][group][outcome]
    tally: dict[str, dict[str, dict[str, int]]] = {m: defaultdict(lambda: defaultdict(int)) for m in methods}
    rows_out = []
    for label in labels:
        s = system[label["ncessch"]]
        ops = s["operators"]
        base = ops[0] if len(ops) == 1 else None
        ambiguous = len(ops) > 1
        line = {"school": label.get("school", label["ncessch"]), "group": label["group"],
                "truth": "no nonprofit" if label["no_nonprofit"] else (label["operator_ein"] or "?")}
        for m in methods:
            if m != "matcher" and label["ncessch"] in decided[m]:
                answer, amb = decided[m][label["ncessch"]], False
            else:
                answer, amb = base, ambiguous
            outcome = operator_outcome(label, answer, amb)
            tally[m][label["group"]][outcome] += 1
            tally[m]["all"][outcome] += 1
            if label.get("confidence", "").lower() != "low":
                tally[m]["all (excl. low confidence)"][outcome] += 1
            line[m] = outcome
        mgr = manager_outcome(label, s["managers"])
        tally["matcher"]["management claim"][mgr] += 1
        line["truth_in_candidates"] = bool(label["operator_ein"] and label["operator_ein"] in s["linked"])
        rows_out.append(line)

    def fmt(counts: dict[str, int]) -> str:
        labelled = sum(v for k, v in counts.items() if k != "unlabelled")
        return f"{counts.get('right', 0)} of {labelled}" + (
            " (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()) if k not in ("right", "unlabelled") and v) + ")"
            if any(v for k, v in counts.items() if k not in ("right", "unlabelled")) else "")

    lines = [f"# Eval results\n", f"Labels: `{path.relative_to(REPO_ROOT)}` ({len(labels)} schools).\n",
             "Operator: right / of labelled; other outcomes in brackets "
             "(ambiguous = several tied, missed = said none but one exists, false_link = linked but there is none).\n",
             "| Group | " + " | ".join(methods) + " |", "|---|" + "---|" * len(methods)]
    for g in [*GROUPS, "all", "all (excl. low confidence)"]:
        lines.append(f"| {g} | " + " | ".join(fmt(tally[m][g]) for m in methods) + " |")
    lines.append(f"\nManagement company claims (matcher): {fmt(tally['matcher']['management claim'])}.\n")
    found = sum(1 for r in rows_out if r["truth_in_candidates"])
    with_ein = sum(1 for r in labels if r["operator_ein"])
    lines.append(f"The true operator is among the matcher's linked nonprofits for {found} of {with_ein} labelled schools.\n")
    lines.append("## Per school\n")
    lines.append("| School | Group | Truth | " + " | ".join(methods) + " |")
    lines.append("|---|---|---|" + "---|" * len(methods))
    for r in rows_out:
        lines.append(f"| {r['school']} | {r['group']} | {r['truth']} | " + " | ".join(r[m] for m in methods) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    report = evaluate(os.environ.get("DATABASE_URL", "postgresql://localhost/charter990"))
    (EVAL_DIR / "results.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
