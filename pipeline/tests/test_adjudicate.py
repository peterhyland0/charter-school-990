import pytest

from charter990 import jev
from charter990.assets import adjudicate
from charter990.assets.adjudicate import (
    JEV_ANY_AT,
    JEV_PICK_AT,
    LOW_CONFIDENCE,
    SCHOOL_LEADER_TITLE,
    SCORE_FLOOR,
    call_jev,
    decide,
    jev_decision,
    jev_probabilities,
    jev_questions,
    jev_state,
    option_key,
    mark_largest_revenue,
    picks,
    rationale,
    schedule_o_names_school,
    score,
    street_key,
    title_names_school,
)


def feats(**overrides) -> dict:
    base = {
        "signals": 0, "name_similarity": 0.0, "full_name_similarity": 0.0, "same_website": False,
        "same_stem": False, "same_phone": False, "same_street": False, "same_zip": False, "same_city": False,
        "in_state": True, "mgmt_delegated": None, "revenue": 5_000_000, "title_names_school": False,
        "authorizer_names_candidate": False, "schedule_o_names_school": False, "nums_disagree": False,
        "support_name": False, "expenses": None, "largest_revenue": False, "school_leader_title": False,
    }
    return base | overrides


def cand(ein: str, **overrides) -> dict:
    return {"ein": ein, "name": f"ORG {ein}", "features": feats(**overrides)}


@pytest.mark.parametrize("a, b", [
    ("3725 Airport Blvd", "3725 AIRPORT BLVD STE 100"),
    ("542 7TH AVE W", "542 West 7th Avenue"),
    ("16255 VENTURA BLVD 1212", "16255 Ventura Boulevard, Suite 1212"),
    ("108 North Street", "108 NORTH ST"),
])
def test_street_key_agrees(a, b):
    assert street_key(a) == street_key(b) is not None


def test_street_key_differs_and_rejects_boxes():
    assert street_key("3725 AIRPORT BLVD") != street_key("3727 AIRPORT BLVD")
    assert street_key("PO BOX 95") is None and street_key(None) is None and street_key("MAIN ST") is None


def test_title_names_school_needs_words_beyond_the_candidates_name():
    titles = ["CEO", "Head of School, Wrightwood Campus"]
    assert title_names_school("NOBLE WRIGHTWOOD", "NOBLE NETWORK", titles)
    assert not title_names_school("NOBLE WRIGHTWOOD", "NOBLE NETWORK", ["CEO", "TREASURER"])
    # Every distinctive word is already in the candidate's name: nothing new.
    assert not title_names_school("WRIGHTWOOD", "WRIGHTWOOD", ["Head of School, Wrightwood"])


def test_schedule_o_names_school():
    text = "PART I, LINE 1: THE ORGANIZATION OPERATES KIPP ASCEND PRIMARY AND KIPP ONE ACADEMY."
    assert schedule_o_names_school("KIPP Ascend Primary", "KIPP ASCEND PRIMARY", "KIPP CHICAGO", text)
    assert schedule_o_names_school("Ascend Primary School", "ASCEND PRIMARY", "KIPP CHICAGO", text)
    assert not schedule_o_names_school("KIPP Bloom", "KIPP BLOOM", "KIPP CHICAGO", text)
    assert not schedule_o_names_school("KIPP Bloom", "KIPP BLOOM", "KIPP CHICAGO", None)


def test_score_adds_weighted_features():
    total, parts = score(feats(same_website=True, same_street=True, name_similarity=0.5, revenue=100_000))
    assert parts == {"same_website": 3.0, "same_street": 2.0, "name_similarity": 2.0, "in_state": 1.0,
                     "small_revenue": -1.0}
    assert total == 7.0


def test_decide_picks_a_clear_leader():
    chosen, conf, reason = decide([
        cand("111111111", same_street=True, same_phone=True, name_similarity=0.6),
        cand("222222222", name_similarity=0.6),
    ])
    assert chosen == "111111111" and 0.5 < conf <= 0.99 and "same_street" in reason


def test_decide_none_below_floor_or_without_a_margin():
    chosen, conf, _ = decide([cand("111111111", name_similarity=0.4)])
    assert chosen is None and conf == pytest.approx(1 - 2.6 / SCORE_FLOOR, abs=0.01)
    tie = [cand("111111111", same_website=True, name_similarity=0.8),
           cand("222222222", same_website=True, name_similarity=0.75)]
    chosen, conf, reason = decide(tie)
    assert chosen is None and conf == LOW_CONFIDENCE and "leads by only" in reason
    assert decide([]) == (None, 1.0, "No candidates.")


def test_decide_needs_more_than_a_name():
    # "ROLLING HILLS" middle school against a private prep school of that name.
    chosen, _, reason = decide([cand("111111111", name_similarity=1.0, full_name_similarity=0.5)])
    assert chosen is None and "only on its name" in reason
    chosen, _, _ = decide([cand("111111111", name_similarity=1.0, full_name_similarity=0.95)])
    assert chosen == "111111111"


def test_school_leader_title_and_largest_revenue():
    assert SCHOOL_LEADER_TITLE.search("HEAD OF SCHOOL") and SCHOOL_LEADER_TITLE.search("PRINCIPAL/CEO")
    assert not SCHOOL_LEADER_TITLE.search("BOARD CHAIR") and not SCHOOL_LEADER_TITLE.search("TREASURER")
    # An operator beside its own same-named foundation.
    rows = [{"ein": "273430689", "features": feats(revenue=8_100_000)},
            {"ein": "841262197", "features": feats(revenue=150_000)}]
    mark_largest_revenue(rows)
    assert [r["features"]["largest_revenue"] for r in rows] == [True, False]
    close = [{"ein": "1", "features": feats(revenue=5_000_000)}, {"ein": "2", "features": feats(revenue=4_000_000)}]
    mark_largest_revenue(close)
    assert not any(r["features"]["largest_revenue"] for r in close)


def jev_row(ein: str, name: str, **overrides) -> dict:
    return {"ein": ein, "name": name, "dba": None, "city": "EDWARDS", "state": "CO", "website": None,
            "street": "123 MAIN ST", "titles": ["PRINCIPAL"], "signals": ["website"], "school_core": "EAGLE COUNTY",
            "core": "EAGLE COUNTY", "revenue": 8_100_000, "features": feats(**overrides)}


def test_jev_questions_ask_whether_any_and_which():
    rows = [jev_row("273430689", "EAGLE COUNTY CHARTER ACADEMY"), jev_row("841262197", "EAGLE COUNTY FOUNDATION")]
    qs = jev_questions(rows)
    assert qs["operator_exists"]["type"] == "noul" and set(qs["operator_exists"]["criteria"]) == {"true", "false"}
    choice = qs["operator"]
    assert choice["type"] == "choice" and "none" not in choice["criteria"]
    key = option_key(rows[0])
    assert key == "eagle_county_charter_academy_273430689"
    assert "EIN 273430689" in choice["criteria"][key] and "$8,100,000" in choice["criteria"][key]
    # One candidate: only the yes/no.
    assert set(jev_questions(rows[:1])) == {"operator_exists"}


def test_jev_state_keys_candidates_by_choice_name():
    payload = {"school": {"name": "EAGLE COUNTY CHARTER ACADEMY"},
               "candidates": [{"ein": "273430689", "name": "EAGLE COUNTY CHARTER ACADEMY", "features": {}}]}
    state = jev_state("ambiguous", payload)
    assert list(state["candidates"]) == ["eagle_county_charter_academy_273430689"]
    assert "equally strong" in state["how_candidates_were_found"]


def test_jev_decision_needs_both_thresholds():
    assert jev_decision(0.9, {"111111111": 0.8, "222222222": 0.2}) == ("111111111", 0.72)
    # Jev doubts any candidate runs the school.
    assert jev_decision(JEV_ANY_AT - 0.1, {"111111111": 0.9, "222222222": 0.1}) == (None, 0.4)
    # It does, but can't tell which.
    assert jev_decision(0.9, {"111111111": JEV_PICK_AT - 0.1, "222222222": 0.5}) == (None, 0.1)


def test_jev_probabilities_map_choice_names_to_eins():
    rows = [jev_row("273430689", "OPERATOR"), jev_row("841262197", "FOUNDATION")]
    answers = {"operator_exists": {"noul": 0.8},
               "operator": {"probabilities": {option_key(rows[0]): 0.85, option_key(rows[1]): 0.15}}}
    assert jev_probabilities(answers, rows) == (0.8, {"273430689": 0.85, "841262197": 0.15})
    assert jev_probabilities({"operator_exists": {"noul": 0.4}}, rows[:1]) == (0.4, {"273430689": 1.0})


def test_rationale_is_written_from_the_evidence():
    rows = [jev_row("273430689", "OPERATOR", same_street=True, largest_revenue=True, revenue=8_100_000,
                    school_leader_title=True, name_similarity=1.0),
            jev_row("841262197", "FOUNDATION", revenue=150_000)]
    shares = {"273430689": 0.9, "841262197": 0.1}
    text = rationale(0.8, shares, "273430689", rows)
    assert text.startswith("Jev puts 80% on a candidate running the school, and 90% of that on OPERATOR.")
    assert "same street (123 MAIN ST)" in text and "largest revenue" in text and "Next: FOUNDATION 10%." in text
    assert rationale(0.5, shares, None, rows).startswith("None: Jev puts 50% on any candidate running the school")


def test_call_jev_records_the_version_that_answered(monkeypatch):
    rows = [jev_row("273430689", "OPERATOR", same_phone=True), jev_row("841262197", "FOUNDATION")]

    def fake_ask(state, questions):
        assert set(questions) == {"operator_exists", "operator"}
        return {"model": "jev-1.14.0", "usage": {"input_tokens": 900},
                "answers": {"operator_exists": {"type": "noul", "noul": 0.9},
                            "operator": {"choice": option_key(rows[0]), "confidence": 0.9,
                                         "probabilities": {option_key(rows[0]): 0.9, option_key(rows[1]): 0.1}}}}

    monkeypatch.setattr(jev, "ask", fake_ask)
    (chosen, conf, reason), version, tokens = call_jev({"school": {}}, jev_questions(rows), rows)
    assert (chosen, conf, version, tokens) == ("273430689", 0.81, "jev-1.14.0", 900)
    assert "same phone" in reason


def test_picks_apply_one_method_or_where_both_agree():
    keys = [("ambiguous", "A"), ("unmatched", "B"), ("unmatched", "C"), ("unmatched", "D"), ("ambiguous", "E")]
    current = {
        # Both pick the same nonprofit.
        ("scored", "A"): ("111111111", 0.8, "scores 6", "scored-v1"),
        ("jev", "A"): ("111111111", 0.72, "Jev puts 90%", "jev-1.14.0"),
        # They disagree.
        ("scored", "B"): ("222222222", 0.7, "scores 5", "scored-v1"),
        ("jev", "B"): ("333333333", 0.65, "Jev puts 80%", "jev-1.14.0"),
        # Jev passes.
        ("scored", "C"): ("444444444", 0.6, "scores 5", "scored-v1"),
        ("jev", "C"): (None, 0.5, "None: Jev puts 50%", "jev-1.14.0"),
        # No Jev decision about the current inputs.
        ("scored", "D"): ("555555555", 0.9, "scores 8", "scored-v1"),
        # Both pass.
        ("scored", "E"): (None, 0.3, "leads by only", "scored-v1"),
        ("jev", "E"): (None, 0.4, "None", "jev-1.14.0"),
    }
    assert picks("both", keys, current) == [("A", "111111111", "ambiguous", {
        "method": "both",
        "scored": {"model": "scored-v1", "confidence": 0.8, "reason": "scores 6"},
        "jev": {"model": "jev-1.14.0", "confidence": 0.72, "reason": "Jev puts 90%"},
    })]
    assert [p[:2] for p in picks("scored", keys, current)] == [
        ("A", "111111111"), ("B", "222222222"), ("C", "444444444"), ("D", "555555555")]
    assert [p[:2] for p in picks("jev", keys, current)] == [("A", "111111111"), ("B", "333333333")]
    # One method: its decision, flat, as before.
    assert picks("jev", keys, current)[1][3] == {
        "method": "jev", "model": "jev-1.14.0", "confidence": 0.65, "reason": "Jev puts 80%"}


def test_jev_env(monkeypatch):
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY", "JEV_API_URL", "TYPESAFE_BASE_URL", "TYPESAFE_DEFAULT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert not jev.available() and jev.model() == "jev-latest" and jev.base_url() == "https://api.typesafe.ai"
    monkeypatch.setenv("JEV_API_URL", "https://example.test/v1/systemone/")
    assert jev.base_url() == "https://example.test"
    assert adjudicate.jev is jev
