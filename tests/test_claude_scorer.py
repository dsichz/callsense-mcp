"""The claude scorer with a fake client. No network, no key, no cost."""
import json
from types import SimpleNamespace

import pytest

from callsense_mcp.scorers import DEFAULT_MODEL
from callsense_mcp.store import CallSenseError

FIXED = [
    {"rule_id": "next_step_committed", "value": True, "quote": "Wednesday at eleven works, I'll bring our warehouse manager.", "turn": 6},
    {"rule_id": "objection_handled", "value": False, "quote": "", "turn": 0},
    {"rule_id": "qualification_asked", "value": True,
     "quote": "Who signs off on the order for the new warehouse, you or the owner?", "turn": 3},
    {"rule_id": "churn_risk", "value": False, "quote": "", "turn": 0},
]
# first answer quotes the rep's proposal as the customer's commitment
FIRST = [dict(FIXED[0], quote="Can we go through them on Wednesday at eleven?", turn=5)] + FIXED[1:]


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        text = json.dumps({"scores": self.answers.pop(0)})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def test_rejections_are_fed_back_and_the_fixed_set_is_saved(app):
    client = FakeClient([FIRST, FIXED])
    out = app.score_call("call_008", scorer="claude", client=client)

    assert out["accepted"] is True and out["saved"] is True
    assert out["scorer"] == f"claude:{DEFAULT_MODEL}"
    assert [a["accepted"] for a in out["attempts"]] == [False, True]
    assert out["attempts"][0]["rejected"][0]["reason"] == (
        "turn 5 is the rep speaking; next_step_committed needs the customer's own words"
    )
    feedback = client.requests[1]["messages"][-1]["content"]
    assert "Hint: customer turns in this call: 2, 4, 6, 8" in feedback
    first = client.requests[0]
    assert first["model"] == DEFAULT_MODEL
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert app.store.scores("call_008")["scored_by"] == f"claude:{DEFAULT_MODEL}"


def test_nothing_is_saved_when_the_model_does_not_fix_it(app):
    out = app.score_call("call_008", scorer="claude", client=FakeClient([FIRST, FIRST]))
    assert out["accepted"] is False and out["saved"] is False
    assert len(out["attempts"]) == 2
    assert app.store.scores("call_008") is None


def test_model_comes_from_env(app, monkeypatch):
    monkeypatch.setenv("SCORER_MODEL", "claude-opus-5")
    client = FakeClient([FIXED])
    app.score_call("call_008", scorer="claude", client=client)
    assert client.requests[0]["model"] == "claude-opus-5"


def test_without_a_key_the_error_says_what_to_do(app):
    with pytest.raises(CallSenseError, match="ANTHROPIC_API_KEY") as err:
        app.score_call("call_008", scorer="claude")
    assert "submit_scores" in str(err.value)
    assert app.store.scores("call_008") is None
