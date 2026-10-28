from charter990.evaluate import method_answer, operator_outcome

LABEL = {"no_nonprofit": False, "operator_ein": "273430689"}


def test_a_none_on_a_tied_school_is_an_abstention():
    answer, amb = method_answer(True, None, None, True)
    assert (answer, amb) == (None, True)
    assert operator_outcome(LABEL, answer, amb) == "ambiguous"


def test_a_pick_on_a_tied_school_is_scored():
    assert operator_outcome(LABEL, *method_answer(True, "273430689", None, True)) == "right"
    assert operator_outcome(LABEL, *method_answer(True, "841262197", None, True)) == "wrong"


def test_a_none_on_an_unmatched_school_still_counts():
    assert operator_outcome(LABEL, *method_answer(True, None, None, False)) == "missed"
    no_nonprofit = {"no_nonprofit": True, "operator_ein": ""}
    assert operator_outcome(no_nonprofit, *method_answer(True, None, None, False)) == "right"


def test_no_decision_keeps_the_matchers_answer():
    assert method_answer(False, None, "273430689", False) == ("273430689", False)
