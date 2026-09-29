import copy
from pathlib import Path

import pytest

from callsense_mcp.guard import check_scores, normalize
from callsense_mcp.store import Store

STORE = Store(Path(__file__).resolve().parent.parent)
CALL = STORE.call("call_001")
V1 = STORE.rules(1)
V2 = STORE.rules(2)

GOOD = [
    {"rule_id": "next_step_committed", "value": True, "quote": "Send the offer by Thursday and I will sign on Friday.", "turn": 6},
    {"rule_id": "objection_handled", "value": True, "quote": "On price, the difference is the coating.", "turn": 3},
    {"rule_id": "qualification_asked", "value": True, "quote": "Who approves the purchase on your side, you or the plant director?", "turn": 5},
    {"rule_id": "churn_risk", "value": True, "quote": "we got a quote from another supplier and it is cheaper by about eight percent", "turn": 2},
]


def with_score(rule_id, **changes):
    scores = copy.deepcopy(GOOD)
    for s in scores:
        if s["rule_id"] == rule_id:
            s.update(changes)
    return scores


def only_rejection(result):
    assert not result.accepted
    assert result.scores == []
    assert len(result.rejected) == 1, result.rejected
    return result.rejected[0]


def test_exact_quotes_pass():
    result = check_scores(CALL, V2, GOOD)
    assert result.accepted
    assert result.rejected == [] and result.missing == []
    assert [s["rule_id"] for s in result.scores] == [r["id"] for r in V2["rules"]]


def test_whitespace_case_and_typographic_quotes_are_normalised():
    scores = with_score("next_step_committed", quote="  \u201cSEND the offer   by thursday and I will sign on friday\u201d ")
    result = check_scores(CALL, V2, scores)
    assert result.accepted
    # the stored quote is the call's own wording, not the model's spelling of it
    assert result.scores[0]["quote"] == "Send the offer by Thursday and I will sign on Friday"


def test_paraphrase_is_rejected_with_closest_sentence():
    scores = with_score("next_step_committed", quote="Send the offer by Thursday and I'll sign on Friday.")
    rej = only_rejection(check_scores(CALL, V2, scores))
    assert rej["rule_id"] == "next_step_committed"
    assert rej["reason"] == "quote not found anywhere in the call"
    assert rej["hint"].startswith("closest sentence is in turn 6 (customer)")
    assert "Send the offer by Thursday and I will sign on Friday." in rej["hint"]


def test_invented_quote_gets_no_fake_suggestion():
    scores = with_score("next_step_committed", quote="The customer agreed to sign after the demo next month.")
    rej = only_rejection(check_scores(CALL, V2, scores))
    assert rej["hint"].startswith("nothing in the call is close")


def test_quote_from_another_turn_says_where_it_is():
    scores = with_score("next_step_committed", turn=4)
    rej = only_rejection(check_scores(CALL, V2, scores))
    assert rej["reason"] == "quote is not in turn 4"
    assert rej["hint"] == "found in turn 6"


def test_missing_or_impossible_turn_number_is_rejected():
    rej = only_rejection(check_scores(CALL, V2, with_score("next_step_committed", turn=None)))
    assert rej["reason"] == "no turn number" and rej["hint"] == "found in turn 6"
    rej = only_rejection(check_scores(CALL, V2, with_score("next_step_committed", turn=9)))
    assert "does not exist" in rej["reason"]


def test_wrong_speaker_is_rejected():
    # the rep explaining the price gap is not the customer signalling churn
    scores = with_score("churn_risk", quote="theirs is one side, which is why the eight percent", turn=3)
    rej = only_rejection(check_scores(CALL, V2, scores))
    assert rej["rule_id"] == "churn_risk"
    assert rej["reason"] == "turn 3 is the rep speaking; churn_risk needs the customer's own words"
    assert rej["hint"] == "customer turns in this call: 2, 4, 6"


def test_found_elsewhere_but_only_in_the_wrong_speakers_turn():
    scores = with_score("churn_risk", quote="On price, the difference is the coating.", turn=2)
    rej = only_rejection(check_scores(CALL, V2, scores))
    assert rej["hint"].startswith("found in turn 3, but that is the rep speaking")


def test_true_with_empty_quote_is_rejected_when_quotes_are_required():
    rej = only_rejection(check_scores(CALL, V2, with_score("objection_handled", quote="", turn=None)))
    assert rej["reason"] == "true score without a quote"


def test_true_with_empty_quote_passes_under_v1_which_did_not_require_quotes():
    assert check_scores(CALL, V1, with_score("objection_handled", quote="", turn=None)).accepted


def test_false_with_empty_quote_is_fine():
    scores = with_score("churn_risk", value=False, quote="", turn=None)
    result = check_scores(CALL, V2, scores)
    assert result.accepted
    assert result.scores[-1] == {"rule_id": "churn_risk", "value": False, "quote": "", "turn": None}


def test_quote_too_short_to_stand_as_evidence():
    rej = only_rejection(check_scores(CALL, V2, with_score("next_step_committed", quote="by Thursday")))
    assert rej["reason"] == "quote too short to stand as evidence"
    assert "Send the offer by Thursday and I will sign on Friday." in rej["hint"]


def test_missing_rules_are_listed():
    result = check_scores(CALL, V2, GOOD[:2])
    assert not result.accepted
    assert result.rejected == []
    assert result.missing == ["qualification_asked", "churn_risk"]


def test_unknown_and_duplicate_rules_are_rejected():
    scores = GOOD + [{"rule_id": "price_discussed", "value": False}, dict(GOOD[0])]
    result = check_scores(CALL, V2, scores)
    reasons = {r["reason"] for r in result.rejected}
    assert "no rule 'price_discussed' in rules v2" in reasons
    assert "rule scored twice" in reasons


def test_one_bad_score_blocks_the_whole_set():
    scores = with_score("qualification_asked", quote="Who decides on the budget?")
    result = check_scores(CALL, V2, scores)
    assert not result.accepted and result.scores == []


def test_wrong_rules_version_is_refused_and_nothing_is_saved(app):
    before = app.store.scores("call_001")
    out = app.submit_scores("call_001", 7, GOOD)
    assert out["accepted"] is False and out["saved"] is False
    assert out["error"] == "unknown rules version 7; available: 1, 2"
    assert app.store.scores("call_001") == before


def test_unknown_call_is_refused(app):
    out = app.submit_scores("call_999", 2, GOOD)
    assert out["accepted"] is False and "no call call_999" in out["error"]


def test_path_like_call_ids_are_refused(app):
    out = app.submit_scores("../../etc/passwd", 2, GOOD)
    assert out["accepted"] is False and "invalid call id" in out["error"]


@pytest.mark.parametrize(
    "text, expected",
    [("Hello,\n  World", "hello, world"), ("  HELLO   WORLD ", "hello world"), ("It\u2019s \u201cfine\u201d", "it's \"fine\"")],
)
def test_normalize(text, expected):
    assert normalize(text) == expected
