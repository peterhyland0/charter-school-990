"""Hand corrections to school matches.

    python -m charter990.correct add --school 360007702185 --ein 203505981 --action link --note "..." --by "Jane Doe"
    python -m charter990.correct list
    python -m charter990.correct retire --id 3

A correction is stored in match_corrections and applied by the school_matches
asset on every run, so it survives reruns. After a change this rebuilds
school_matches through Dagster, so the run shows up in the Dagster UI.
"""

import argparse
import getpass
import os
import subprocess
import sys

import psycopg

from .checks import CORRECTION_STATE

ACTIONS = ("link", "unlink", "none")


def _connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _rematch() -> None:
    print("rebuilding school_matches ...", flush=True)
    result = subprocess.run(
        ["dagster", "asset", "materialize", "-m", "charter990.definitions", "--select", "school_matches"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or "RUN_SUCCESS" not in result.stderr + result.stdout:
        sys.stderr.write(result.stderr[-4000:])
        sys.exit("rebuilding school_matches failed; the correction is saved and applies on the next run")
    print("done: matches rebuilt; active corrections apply on every run")


def add(school: str, ein: str | None, action: str, note: str, by: str) -> int:
    ein = ein.replace("-", "").zfill(9) if ein else None
    if (action == "none") != (ein is None):
        sys.exit("--ein is required for link and unlink, and not allowed for none")
    if not note.strip():
        sys.exit("--note is required: say why, so the next person can check it")
    with _connect() as conn:
        row = conn.execute("SELECT sch_name FROM schools WHERE ncessch = %s AND is_operating", (school,)).fetchone()
        if row is None:
            sys.exit(f"no operating school with NCES id {school}")
        if ein is not None:
            filer = conn.execute("SELECT org_name FROM latest_filings WHERE ein = %s", (ein,)).fetchone()
            if filer is None:
                sys.exit(f"no 990 filing on file for EIN {ein}, so there is nothing to link")
        # The new correction replaces any it contradicts: one for the same
        # nonprofit, and, for a link or "none", the school's previous answer.
        # One transaction, so a failed insert never loses the old correction.
        with conn.transaction():
            retired = conn.execute(
                """UPDATE match_corrections SET retired_at = now()
                   WHERE ncessch = %(school)s AND retired_at IS NULL
                     AND (coalesce(ein, '') = coalesce(%(ein)s, '')
                          OR (%(action)s IN ('link', 'none') AND action IN ('link', 'none')))
                   RETURNING id""",
                {"school": school, "ein": ein, "action": action},
            ).fetchall()
            (cid,) = conn.execute(
                "INSERT INTO match_corrections (ncessch, ein, action, note, created_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (school, ein, action, note, by),
            ).fetchone()
    target = f" {filer[0]} ({ein})" if ein else ""
    print(f"correction {cid}: {action}{target} for {row[0]} ({school})")
    if retired:
        print("replaces correction " + ", ".join(str(r[0]) for r in retired))
    return cid


def _confirm(cid: int) -> None:
    """Fail loudly if the rebuilt matches don't reflect this correction."""
    with _connect() as conn:
        state = {r[0]: r[5] for r in conn.execute(CORRECTION_STATE).fetchall()}
    if not state.get(cid):
        sys.exit(f"correction {cid} is saved but not reflected in the matches; see the corrections_hold check")


def retire(cid: int) -> None:
    with _connect() as conn:
        n = conn.execute(
            "UPDATE match_corrections SET retired_at = now() WHERE id = %s AND retired_at IS NULL", (cid,)
        ).rowcount
    if not n:
        sys.exit(f"no active correction with id {cid}")
    print(f"correction {cid} retired")


def show() -> None:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT c.id, c.action, c.ncessch, s.sch_name, c.ein, f.org_name, c.note, c.created_by, c.created_at::date
               FROM match_corrections c
               LEFT JOIN schools s USING (ncessch)
               LEFT JOIN latest_filings f ON f.ein = c.ein
               WHERE c.retired_at IS NULL ORDER BY c.id"""
        ).fetchall()
    if not rows:
        print("no active corrections")
    for cid, action, school, sch_name, ein, org, note, by, at in rows:
        target = f" -> {org or '?'} ({ein})" if ein else ""
        print(f"{cid:>4}  {action:<6}  {sch_name or '?'} ({school}){target}  [{by}, {at}] {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add", help="add a correction and rebuild matches")
    p_add.add_argument("--school", required=True, help="NCES school id (NCESSCH)")
    p_add.add_argument("--ein", help="nonprofit EIN; omit for --action none")
    p_add.add_argument("--action", choices=ACTIONS, default="link")
    p_add.add_argument("--note", required=True)
    p_add.add_argument("--by", default=os.environ.get("CORRECTED_BY") or getpass.getuser(),
                       help="who made the fix (default: $CORRECTED_BY or your login)")
    p_retire = sub.add_parser("retire", help="undo a correction and rebuild matches")
    p_retire.add_argument("--id", type=int, required=True)
    sub.add_parser("list", help="list active corrections")
    args = parser.parse_args()

    if args.command == "list":
        show()
        return
    if args.command == "add":
        cid = add(args.school, args.ein, args.action, args.note, args.by)
        _rematch()
        _confirm(cid)
    else:
        retire(args.id)
        _rematch()


if __name__ == "__main__":
    main()
