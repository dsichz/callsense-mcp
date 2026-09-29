from pathlib import Path

import pytest

from callsense_mcp.__main__ import main
from callsense_mcp.store import CallSenseError

V2_TEXT = (Path(__file__).resolve().parent.parent / "rules" / "v2.yaml").read_text(encoding="utf-8")


def test_baseline_on_v2_passes_the_gate(app):
    out = app.run_eval(rules_version=2, scorer="baseline", min_agreement=0.8)
    assert out["passed"] is True
    assert out["overall_agreement"] >= 0.8
    assert out["graded_calls"] == [f"call_00{i}" for i in range(1, 7)]
    assert {row["rule_id"]: row["agreement"] for row in out["per_rule"]} == {
        "next_step_committed": 1.0, "objection_handled": 1.0, "qualification_asked": 1.0, "churn_risk": 1.0,
    }
    assert out["misses"] == []


def test_draft_with_an_unlabeled_rule_cannot_pass(app):
    draft = V2_TEXT.replace("version: 2", "version: 3") + (
        "  - id: price_discussed\n"
        "    speaker: rep\n"
        "    question: Did the rep state the price?\n"
        "    evidence: A number with a currency.\n"
        "    keywords: [price]\n"
    )
    out = app.run_eval(rules_yaml=draft)
    assert out["rules"] == "draft v3, not saved"
    assert out["passed"] is False
    assert out["unlabeled_rules"] == ["price_discussed"]
    assert not (app.store.rules_dir / "v3.yaml").exists()


def test_draft_that_breaks_a_rule_fails_even_when_the_total_looks_fine(app):
    # next step moved to the rep's words: the rep promising a call is not the customer committing
    draft = V2_TEXT.replace("  - id: next_step_committed\n    speaker: customer", "  - id: next_step_committed\n    speaker: rep")
    out = app.run_eval(rules_yaml=draft)
    assert out["overall_agreement"] >= 0.8  # the total alone would have let it through
    assert out["passed"] is False
    assert out["failing_rules"] == ["next_step_committed"]
    assert {m["call_id"] for m in out["misses"]} == {"call_001", "call_003", "call_004"}


def test_eval_input_errors(app):
    with pytest.raises(CallSenseError, match="not both"):
        app.run_eval(rules_version=2, rules_yaml=V2_TEXT)
    with pytest.raises(CallSenseError, match="between 0 and 1"):
        app.run_eval(min_agreement=80)
    with pytest.raises(CallSenseError, match="not valid YAML"):
        app.run_eval(rules_yaml="version: [2")
    with pytest.raises(CallSenseError, match="speaker"):
        app.run_eval(rules_yaml=V2_TEXT.replace("speaker: rep", "speaker: manager"))
    with pytest.raises(CallSenseError, match="ANTHROPIC_API_KEY"):
        app.run_eval(rules_version=2, scorer="claude")


def test_cli_gate_exit_codes(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CALLSENSE_HOME", str(home))
    assert main(["eval", "--rules-version", "2"]) == 0
    assert "overall agreement 100%" in capsys.readouterr().out
    draft = tmp_path / "draft.yaml"
    draft.write_text(V2_TEXT.replace("  - id: next_step_committed\n    speaker: customer", "  - id: next_step_committed\n    speaker: rep"))
    assert main(["eval", "--rules-file", str(draft)]) == 1
