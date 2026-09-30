# callsense-mcp

An MCP server that turns Claude (Claude Desktop, Claude Code, or an agent built on the Agent SDK) into a sales-call analyst for a team's own playbook. Claude reads the call and scores it. The server decides what gets saved: a score is accepted only with a quote copied from the call, said by the right side of the conversation.

It is built on [call-scoring-harness](https://github.com/dsichz/call-scoring-harness), the evaluation pattern behind a call-intelligence pipeline that has been running for a manufacturer's sales team since June 2026. The rules, the synthetic calls and the quote check come from there.

## Why the evidence guard is the product

The first production version was killed in its first week over a single score the reps saw as unfair. A score the reps cannot trace to a sentence in their own call gets argued with and then ignored. The pipeline came back when every score had its source quote next to it.

A model in a chat can write a fluent score whether or not the call supports it, so the server never takes the model's word:

1. **A quote or no score.** A true score needs text copied word for word from one numbered turn. Spaces and case are ignored, nothing else is.
2. **The right speaker.** Churn risk has to be the customer's words. Handling an objection has to be the rep's. Each rule names its speaker, and the server checks who said the turn.
3. **All or nothing.** Every rule of the version has to be scored, and one bad score blocks the set. Nothing half-checked reaches the store or the deal card.
4. **Rejections the model can act on.** A rejected score comes back with a reason and a hint: the turn where the quote really is, or the closest sentence in the call. Claude fixes it and submits again.
5. **Verdicts are code.** At risk, follow up or healthy is computed from the accepted scores by a fixed table, so the same scores always give the same verdict.

## A rejection

Real output from `tests/test_server.py`. The submission cleaned a filler out of the customer's words and counted the customer's own remark as the rep's qualification question:

```json
{
  "accepted": false,
  "saved": false,
  "call_id": "call_007",
  "rules_version": 2,
  "rejected": [
    {
      "rule_id": "qualification_asked",
      "reason": "turn 6 is the customer speaking; qualification_asked needs the rep's own words",
      "hint": "rep turns in this call: 1, 3, 5, 7"
    },
    {
      "rule_id": "churn_risk",
      "reason": "quote not found anywhere in the call",
      "hint": "closest sentence is in turn 2 (customer): \"So we are, uh, thinking about moving the October volume to them.\" Copy it exactly, or score churn_risk false."
    }
  ],
  "missing": [],
  "next": "Nothing was saved. Fix the rejected rules, add any missing ones, and submit the full set again."
}
```

A quote placed in the wrong turn gets `"hint": "found in turn 6"`.

## Tools

| Tool | What it does |
|---|---|
| `list_calls(rep, scored)` | Calls with date, rep, customer, number of turns, whether scored, verdict. |
| `get_call(call_id)` | Numbered turns `{n, speaker, text}`. |
| `get_rules(version)` | A rule set: id, question, evidence, speaker, and whether quotes are required. Latest by default. |
| `submit_scores(call_id, rules_version, scores)` | The evidence guard. Saves the set only if every score passes, otherwise returns `rejected[{rule_id, reason, hint}]` and `missing[]`. |
| `score_call(call_id, scorer)` | Scoring on the server for runs nobody watches: `baseline` (keywords, no key) or `claude` (Anthropic API, needs `ANTHROPIC_API_KEY`, model from `SCORER_MODEL`, default `claude-sonnet-5`). Same guard. The claude scorer gets the guard's hints back once. |
| `team_report(rep, since)` | Rate per rule, a table per rep, calls at risk with their quotes, follow-ups, unscored calls. Every count lists the call ids behind it. |
| `run_eval(rules_version, rules_yaml, scorer, min_agreement)` | Agreement with the hand-graded labels and quote validity, pass or fail. `rules_yaml` measures a draft rule set without saving it. |
| `deal_card_note(call_id)` | The deal-card note and CRM field payload in the shape of the harness write-back. Builds text only, sends nothing. |

Resources: `callsense://rules/{version}` (the YAML file) and `callsense://calls/{call_id}`.

Prompts: `coach_rep(rep)` writes a weekly coaching note from the rep's scored calls, with their quotes. `score_unscored()` scores every unscored call through the guard and ends with the team report.

## Verdict table

`callsense_mcp/verdict.py`. If an at-risk row fires, the call is at risk. If not and the follow-up row fires, it is follow up. Otherwise it is healthy.

| Verdict | When | Reason shown, with the customer's quote |
|---|---|---|
| at risk | churn_risk yes and objection_handled no | customer signalled leaving and the objection was not answered |
| at risk | churn_risk yes and next_step_committed no | customer signalled leaving and no next step was agreed |
| follow up | next_step_committed no | no next step agreed |
| healthy | none of the above | |

Flags are shown whatever the verdict: churn signal (with its quote), no next step, not qualified.

## Connect

Python 3.10 or newer, MCP Python SDK 2.x.

```bash
git clone https://github.com/dsichz/callsense-mcp && cd callsense-mcp
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"      # ".[dev,claude]" to enable the server-side Claude scorer
```

Claude Code:

```bash
claude mcp add callsense -- "$PWD/.venv/bin/python" -m callsense_mcp
```

Then ask it to score the unscored calls, or run the prompt as `/mcp__callsense__score_unscored`.

Claude Desktop, in `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS:

```json
{
  "mcpServers": {
    "callsense": {
      "command": "/absolute/path/to/callsense-mcp/.venv/bin/python",
      "args": ["-m", "callsense_mcp"]
    }
  }
}
```

The server reads `rules/` and `data/` from the repository. To point it at another folder with the same layout, set `CALLSENSE_HOME` (`-e CALLSENSE_HOME=...` for `claude mcp add`, an `env` block in the Desktop config).

## Tests and evals

```bash
.venv/bin/pytest -q                                          # 48 tests, no network, no API key
.venv/bin/python -m callsense_mcp eval --rules-version 2     # exits 1 below the gate
.venv/bin/python -m callsense_mcp eval --rules-file draft.yaml
```

The tests cover the guard (exact quote, paraphrase, quote from another turn, wrong speaker, true score without a quote, missing rules, unknown rules version), the server end to end through an in-memory MCP client and over stdio, the eval gate, and the Claude scorer's retry loop against a fake client. The paid API is never called.

The gate applies to every rule and to the total. A draft that moves `next_step_committed` to the rep's words keeps the total at 88% and still fails, because that one rule drops to 50%. There is a test for exactly that. A rule with no human labels cannot pass at all.

The baseline scores 100% on the six graded calls. As in the harness, that says something about the set, not about keyword scoring.

## A live run

[`examples/claude_code_session.md`](examples/claude_code_session.md) is one recorded Claude Code session (model claude-opus-5-5) that scored the two unscored calls and summarised the team report. The guard had nothing to reject in that run. The log says so, and notes what the run did show: Claude's chat summary shortened a quote the guard had kept whole, and Claude guessed a verdict that the verdict table contradicts.

## Data

All calls are synthetic. call_001 to call_006 and the labels in `data/graded/` are copied from call-scoring-harness: B2B sales calls in manufacturing, written in English and labelled by hand. call_007 and call_008 were written for this repository and are not in the graded set. Rep and company names are made up. No client data is in this repository.

The shipped store has the six graded calls scored from their human labels (`python -m callsense_mcp seed` rebuilds that) and call_007 and call_008 unscored.

## Layout

| Path | What |
|---|---|
| `callsense_mcp/guard.py` | The evidence guard. |
| `callsense_mcp/verdict.py` | Verdict and flag table. |
| `callsense_mcp/server.py` | MCP tools, resources and prompts. |
| `callsense_mcp/service.py` | The logic behind them. |
| `callsense_mcp/scorers.py` | Baseline and Claude scorers. |
| `callsense_mcp/evals.py` | The eval gate. |
| `callsense_mcp/report.py`, `writeback.py` | Team report, deal-card note. |
| `rules/` | v1 and v2 from the harness, plus `speaker` on every rule. |
| `data/calls/`, `data/graded/`, `data/scores/` | Calls with metadata, hand labels, accepted scores. |
| `examples/` | The recorded Claude Code session. |

## Limits

* The guard proves that a quote is real and comes from the right side. It does not prove the quote supports the rule; that is what `run_eval` measures against human labels. On call_008 the baseline counts "Good. The price stays as in the September quote." as objection handling, a keyword hit with no objection in the call, and the guard lets it through because the rep did say it.
* The graded set is six short calls. The numbers here show the checks work, not how a model scores a real sales floor.
* The store is JSON files for one team, with no auth and no multi-user locking.

Artem Baranov · [theaigency.space](https://theaigency.space) · [linkedin.com/in/artem-es](https://www.linkedin.com/in/artem-es)
