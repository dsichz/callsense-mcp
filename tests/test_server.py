"""The server end to end through an in-memory MCP client (JSON-RPC framing and initialize handshake,
the same path Claude Code and Claude Desktop use over stdio)."""
import asyncio
import json
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from callsense_mcp.server import build_server

TOOLS = {"list_calls", "get_call", "get_rules", "submit_scores", "score_call", "team_report", "run_eval", "deal_card_note"}

# call_007: the customer's churn signal has fillers in it ("um", "uh"), and the rep echoes it in their own words
CHURN_TURN_2 = "So we are, uh, thinking about moving the October volume to them."
GOOD_007 = [
    {"rule_id": "next_step_committed", "value": False, "quote": "", "turn": None},
    {"rule_id": "objection_handled", "value": True,
     "quote": "On our side the delay was the powder coating line, it was down for nine days in August.", "turn": 3},
    {"rule_id": "qualification_asked", "value": False, "quote": "", "turn": None},
    {"rule_id": "churn_risk", "value": True, "quote": CHURN_TURN_2, "turn": 2},
]


def run(server, flow, mode="legacy"):
    async def go():
        async with Client(server, mode=mode) as client:
            return await flow(client)

    return asyncio.run(go())


async def call(client, tool, **args):
    result = await client.call_tool(tool, args)
    return result


def test_lists_tools_resources_and_prompts(home):
    async def flow(c):
        tools = (await c.list_tools()).tools
        templates = (await c.list_resource_templates()).resource_templates
        prompts = (await c.list_prompts()).prompts
        return tools, templates, prompts

    tools, templates, prompts = run(build_server(home), flow)
    assert {t.name for t in tools} == TOOLS
    assert {t.uri_template for t in templates} == {"callsense://rules/{version}", "callsense://calls/{call_id}"}
    assert {p.name for p in prompts} == {"coach_rep", "score_unscored"}
    submit = next(t for t in tools if t.name == "submit_scores")
    score_schema = submit.input_schema["$defs"]["Score"]["properties"]
    assert set(score_schema) == {"rule_id", "value", "quote", "turn"}
    assert all(t.description for t in tools)


def test_submit_scores_rejects_then_accepts(home):
    bad = [
        {"rule_id": "next_step_committed", "value": False, "quote": "", "turn": None},
        GOOD_007[1],
        # the customer volunteered who decides; the rep never asked
        {"rule_id": "qualification_asked", "value": True, "quote": "I need to talk to our production manager first.", "turn": 6},
        # the filler "uh," cleaned out of the customer's words
        {"rule_id": "churn_risk", "value": True, "quote": "So we are thinking about moving the October volume to them.", "turn": 2},
    ]

    async def flow(c):
        first = await call(c, "submit_scores", call_id="call_007", rules_version=2, scores=bad)
        after_first = await call(c, "list_calls", scored=False)
        second = await call(c, "submit_scores", call_id="call_007", rules_version=2, scores=GOOD_007)
        after_second = await call(c, "list_calls", rep="Pavel")
        return first, after_first, second, after_second

    first, after_first, second, after_second = run(build_server(home), flow)

    out = first.structured_content
    print(json.dumps(out, indent=2))  # the README example is this output
    assert first.is_error is False
    assert out["accepted"] is False and out["saved"] is False
    by_rule = {r["rule_id"]: r for r in out["rejected"]}
    assert set(by_rule) == {"qualification_asked", "churn_risk"}
    assert by_rule["qualification_asked"]["reason"] == "turn 6 is the customer speaking; qualification_asked needs the rep's own words"
    assert by_rule["churn_risk"]["reason"] == "quote not found anywhere in the call"
    assert CHURN_TURN_2 in by_rule["churn_risk"]["hint"]
    assert "call_007" in [row["call_id"] for row in after_first.structured_content["calls"]]

    ok = second.structured_content
    assert ok["accepted"] is True and ok["saved"] is True
    assert ok["verdict"] == "at risk"
    assert ok["reasons"][0]["reason"] == "customer signalled leaving and no next step was agreed"
    assert ok["reasons"][0]["evidence"] == {"rule_id": "churn_risk", "turn": 2, "speaker": "customer", "quote": CHURN_TURN_2}
    row = next(r for r in after_second.structured_content["calls"] if r["call_id"] == "call_007")
    assert row["scored"] is True and row["verdict"] == "at risk" and row["scored_by"] == "mcp client: mcp"


def test_modern_protocol_path_gives_the_same_answer(home):
    async def flow(c):
        return await call(c, "submit_scores", call_id="call_007", rules_version=2, scores=GOOD_007)

    result = run(build_server(home), flow, mode="auto")
    assert result.structured_content["accepted"] is True


def test_team_report_counts_trace_to_call_ids(home):
    async def flow(c):
        await call(c, "submit_scores", call_id="call_007", rules_version=2, scores=GOOD_007)
        full = await call(c, "team_report")
        igor = await call(c, "team_report", rep="igor")
        recent = await call(c, "team_report", since="2026-09-18")
        bad_date = await call(c, "team_report", since="last week")
        return full, igor, recent, bad_date

    full, igor, recent, bad_date = run(build_server(home), flow)
    rep = full.structured_content
    assert rep["scored"]["count"] == 7
    assert rep["unscored"] == {"count": 1, "call_ids": ["call_008"]}
    assert [a["call_id"] for a in rep["at_risk"]] == ["call_006", "call_007"]
    assert all(a["reasons"][0]["evidence"]["quote"] for a in rep["at_risk"])
    scored = set(rep["scored"]["call_ids"])
    for row in rep["rules"]:
        assert row["yes"] == len(row["yes_calls"]) and row["of"] == len(row["yes_calls"]) + len(row["no_calls"])
        assert set(row["yes_calls"]) | set(row["no_calls"]) == scored
    for r in rep["by_rep"]:
        assert sorted(sum(r["verdicts"].values(), [])) == sorted(r["scored_calls"])
    assert "7 of 8 calls scored" in rep["summary"]

    assert igor.structured_content["calls_in_scope"] == ["call_003", "call_006"]
    assert recent.structured_content["calls_in_scope"] == ["call_006", "call_007", "call_008"]
    assert bad_date.is_error and "since must be a date" in bad_date.content[0].text


def test_score_call_baseline_goes_through_the_guard(home):
    async def flow(c):
        return await call(c, "score_call", call_id="call_008", scorer="baseline")

    out = run(build_server(home), flow).structured_content
    assert out["accepted"] is True and out["scorer"] == "baseline"
    assert out["verdict"] == "healthy"


def test_score_call_claude_without_key_is_a_clear_error(home):
    async def flow(c):
        result = await call(c, "score_call", call_id="call_008", scorer="claude")
        listed = await call(c, "list_calls", scored=False)
        return result, listed

    result, listed = run(build_server(home), flow)
    assert result.is_error is True
    assert "ANTHROPIC_API_KEY" in result.content[0].text
    assert "call_008" in [r["call_id"] for r in listed.structured_content["calls"]]


def test_unknown_rules_version_through_mcp(home):
    async def flow(c):
        return await call(c, "submit_scores", call_id="call_007", rules_version=3, scores=GOOD_007)

    out = run(build_server(home), flow).structured_content
    assert out["accepted"] is False and out["error"] == "unknown rules version 3; available: 1, 2"


def test_deal_card_note(home):
    async def flow(c):
        note = await call(c, "deal_card_note", call_id="call_006")
        missing = await call(c, "deal_card_note", call_id="call_008")
        return note, missing

    note, missing = run(build_server(home), flow)
    out = note.structured_content
    assert out["sent"] is False and out["verdict"] == "at risk"
    assert "Verdict: AT RISK." in out["note"]
    assert 'customer, turn 2: "We are comparing you with two other offers. Yours is the most expensive."' in out["note"]
    fields = out["deal_update"]["params"]["fields"]
    assert out["deal_update"]["params"]["id"] == 1046
    assert fields["UF_CRM_CHURN_RISK"] == "yes"
    assert fields["UF_CRM_CHURN_RISK_QUOTE"].startswith("We are comparing you")
    assert fields["UF_CRM_SCORING_RULES_VERSION"] == "2"
    assert missing.is_error and "not scored yet" in missing.content[0].text


def test_run_eval_through_mcp(home):
    async def flow(c):
        return await call(c, "run_eval", rules_version=2)

    out = run(build_server(home), flow).structured_content
    assert out["passed"] is True and out["overall_agreement"] == 1.0


def test_resources(home):
    async def flow(c):
        rules = await c.read_resource("callsense://rules/2")
        call_ = await c.read_resource("callsense://calls/call_001")
        try:
            await c.read_resource("callsense://rules/9")
        except Exception as exc:  # the SDK raises MCPError for a missing resource
            missing = exc
        else:
            missing = None
        return rules, call_, missing

    rules, call_, missing = run(build_server(home), flow)
    assert "speaker: customer" in rules.contents[0].text
    body = json.loads(call_.contents[0].text)
    assert body["turns"][0]["n"] == 1 and body["rep"] == "Anna"
    assert missing is not None and "no rules version 9" in str(missing)


def test_prompts(home):
    async def flow(c):
        coach = await c.get_prompt("coach_rep", {"rep": "Anna"})
        unscored = await c.get_prompt("score_unscored")
        return coach, unscored

    coach, unscored = run(build_server(home), flow)
    coach_text = coach.messages[0].content.text
    assert "call_001" in coach_text and "call_004" in coach_text
    assert '"Send the offer by Thursday and I will sign on Friday."' in coach_text
    assert "Unscored right now: call_007, call_008" in unscored.messages[0].content.text


@pytest.mark.parametrize("rep", ["Nobody"])
def test_coach_prompt_for_unknown_rep_fails_clearly(home, rep):
    async def flow(c):
        try:
            await c.get_prompt("coach_rep", {"rep": rep})
        except Exception as exc:
            return exc
        return None

    exc = run(build_server(home), flow)
    assert exc is not None and "'Nobody' has no scored calls" in str(exc)


def test_stdio_entrypoint(home):
    """The command Claude Code and Claude Desktop start: python -m callsense_mcp, talking over stdin/stdout."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "callsense_mcp"], env={"CALLSENSE_HOME": str(home)})

    async def go():
        async with Client(params, mode="legacy") as c:
            tools = await c.list_tools()
            unscored = await c.call_tool("list_calls", {"scored": False})
            return {t.name for t in tools.tools}, unscored.structured_content

    names, unscored = asyncio.run(go())
    assert names == TOOLS
    assert [row["call_id"] for row in unscored["calls"]] == ["call_007", "call_008"]
