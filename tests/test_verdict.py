from callsense_mcp.verdict import AT_RISK, FOLLOW_UP, HEALTHY, compute_verdict


def s(rule_id, value, quote="", turn=None):
    return {"rule_id": rule_id, "value": value, "quote": quote, "turn": turn}


def test_churn_signal_answered_with_a_next_step_is_healthy_but_flagged():
    scores = [s("next_step_committed", True, "Send it by Friday.", 2), s("objection_handled", True, "The price is fixed.", 1),
              s("qualification_asked", True, "Who signs?", 1), s("churn_risk", True, "Another supplier is cheaper.", 2)]
    out = compute_verdict(scores)
    assert out["verdict"] == HEALTHY
    assert [f["flag"] for f in out["flags"]] == ["churn signal"]
    assert out["flags"][0]["evidence"]["quote"] == "Another supplier is cheaper."


def test_churn_signal_left_unanswered_is_at_risk():
    scores = [s("next_step_committed", True, "Ok.", 3), s("objection_handled", False), s("qualification_asked", False),
              s("churn_risk", True, "We are comparing you with two other offers.", 2)]
    out = compute_verdict(scores)
    assert out["verdict"] == AT_RISK
    assert [r["reason"] for r in out["reasons"]] == ["customer signalled leaving and the objection was not answered"]
    assert [f["flag"] for f in out["flags"]] == ["churn signal", "not qualified"]


def test_churn_signal_without_a_next_step_is_at_risk_and_lists_both_reasons_when_both_hold():
    scores = [s("next_step_committed", False), s("objection_handled", False), s("qualification_asked", True, "Who decides?", 1),
              s("churn_risk", True, "We might pause orders.", 2)]
    out = compute_verdict(scores)
    assert out["verdict"] == AT_RISK
    assert len(out["reasons"]) == 2


def test_no_next_step_without_churn_is_follow_up():
    out = compute_verdict([s("next_step_committed", False), s("objection_handled", False),
                           s("qualification_asked", False), s("churn_risk", False)])
    assert out["verdict"] == FOLLOW_UP
    assert [f["flag"] for f in out["flags"]] == ["no next step", "not qualified"]


def test_shipped_store_verdicts(app):
    verdicts = {row["call_id"]: row["verdict"] for row in app.list_calls()["calls"]}
    assert verdicts == {
        "call_001": HEALTHY, "call_002": FOLLOW_UP, "call_003": HEALTHY, "call_004": HEALTHY,
        "call_005": FOLLOW_UP, "call_006": AT_RISK, "call_007": None, "call_008": None,
    }
