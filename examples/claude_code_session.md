# Live run: Claude Code scores two calls through callsense-mcp

> Note: after this run the place name in the synthetic call_007 was changed. The quotes below are updated to match the data; nothing else in the log was edited.

Recorded on 2026-09-29, one run, not repeated or edited. Local paths are replaced with `<store copy>`.

- Client: Claude Code 2.1.282 in print mode (`claude -p`), model `claude-opus-5-5`, no model flag passed.
- Server: this repository over stdio. `CALLSENSE_HOME` pointed at a copy of `rules/` and `data/`, so the store in the repo stayed clean.
- Claude could use only the eight callsense tools: built-in tools off (`--tools ""`), no other MCP servers (`--strict-mcp-config`).
- 7 tool calls, 8 turns, 23 seconds.

## Command

```bash
claude -p "Use the callsense tools. Score the calls that have not been scored yet against the current rules, then run team_report and summarise it for me in a few lines." \
  --mcp-config mcp.json --strict-mcp-config \
  --allowedTools "mcp__callsense__*" --tools "" \
  --permission-mode dontAsk --no-session-persistence \
  --output-format stream-json --verbose
```

`mcp.json`:

```json
{
  "mcpServers": {
    "callsense": {
      "command": "<repo>/.venv/bin/python",
      "args": [
        "-m",
        "callsense_mcp"
      ],
      "env": {
        "CALLSENSE_HOME": "<store copy>"
      }
    }
  }
}
```

## What Claude called

1. `list_calls(scored=false)`: call_007 (Pavel, Severin Glass), call_008 (Anna, Lumen Storage).
2. `get_rules()`: rules v2, `quote_required: true`, four rules with their speaker.
3. `get_call(call_007)` and `get_call(call_008)`, in parallel.
4. `submit_scores(call_007, rules_version=2)`: accepted=true, verdict **at risk**, rejected=[], missing=[].
5. `submit_scores(call_008, rules_version=2)`: accepted=true, verdict **healthy**, rejected=[], missing=[].
6. `team_report()`: "8 of 8 calls scored. At risk: 2 (call_006, call_007). Follow up: 2 (call_002, call_005). Healthy: 4 (call_001, call_003, call_004, call_008). Unscored: 0."

## Guard rejections: none

Both score sets were accepted on the first submission. Claude copied every quote word for word, including the filler "uh," in call_007 turn 2, and every true score came from the side the rule names. The rejection path did not fire in this run, and nothing here pretends it did. It is covered by the tests (paraphrase, quote from another turn, wrong speaker, true score without a quote, missing rules, unknown rules version), and the README shows a real rejection from `tests/test_server.py`.

## Scores as submitted

**call_007**

| rule | value | turn | quote | same as the reading the call was written for |
|---|---|---|---|---|
| next_step_committed | false |  |  | yes |
| objection_handled | true | 3 | On our side the delay was the powder coating line, it was down for nine days in August. It is running again, and I can put your order on the first shift and ship in six days. | yes |
| qualification_asked | false |  |  | yes |
| churn_risk | true | 2 | the plant in Porto says they can ship in five days. So we are, uh, thinking about moving the October volume to them. | yes |

**call_008**

| rule | value | turn | quote | same as the reading the call was written for |
|---|---|---|---|---|
| next_step_committed | true | 6 | Wednesday at eleven works, I'll bring our warehouse manager. | yes |
| objection_handled | false |  |  | yes |
| qualification_asked | true | 3 | Who signs off on the order for the new warehouse, you or the owner? And by when does the racking have to be standing? | yes |
| churn_risk | false |  |  | yes |

The last column compares with what each call was written to show (`data/calls/call_007.json` and `call_008.json` are synthetic calls written for this repo). They are not in the hand-graded eval set.

## The summary Claude wrote

> I scored both unscored calls (call_007 and call_008) against rules v2, and the server accepted both. That makes all 8 calls scored.
>
> **Team report**
> - **Verdicts:** 4 healthy (001, 003, 004, 008), 2 need follow-up (002, 005) and 2 are at risk (006, 007).
> - **At risk:**
>   - **call_007, Pavel / Severin Glass:** the customer said "the plant in Porto says they can ship in five days… thinking about moving the October volume to them." Pavel answered the late deliveries well: he explained the coating-line outage and offered a 6-day ship date or a split delivery. But the customer only agreed to "look at it at some point," so there's no next step.
>   - **call_006, Igor / Granite Yard:** the customer is comparing them against two other offers and called theirs "the most expensive." Nobody answered the price objection.
> - **Pavel needs coaching:** in all 3 of his calls he got no next step and asked no qualification questions. None of his calls are healthy.
> - **Anna:** all 3 calls are healthy, with a next step and qualification questions in every one.
> - **Team rates:** a next step was agreed in 62% of calls, qualification questions were asked in 50%, and an objection was handled in 50%. That last figure is low partly because calls with no objection also count as "no." The customer hinted they might leave in 62% of calls.
>
> **A judgement call you may want to check:** in call_008, the customer mentioned a competitor, but only to say they'd stopped buying from them two years ago and were "staying with you." The rule's evidence list includes any mention of a competitor, but the rule asks whether the customer signalled they may leave. So I scored churn risk as false. If you want the literal reading, call_008 would still show a churn flag but would probably stay healthy, because it has a next step.

## What the run shows, and what it does not

- The guard had nothing to catch. A model at this level, given numbered turns and a tool description that says what will be checked, copied the quotes correctly. The guard earns its place on the runs where that does not happen, and on the unattended `score_call` path.
- In the chat summary Claude shortened the call_007 quote with an ellipsis and dropped the "uh". The guard covers what is saved and what goes to the deal card. The model's own prose in the chat is outside it. `deal_card_note` builds the card from the stored, checked quotes.
- Claude pointed at a wording problem in rules v2: the churn_risk evidence says "Mentions of a competitor", and call_008 mentions a supplier the customer left two years ago. It scored the rule false and said why. That is a candidate wording change for a v3, measured with `run_eval(rules_yaml=...)` before it ships.
- Claude also guessed what the verdict would be under the literal reading: "would still show a churn flag but would probably stay healthy". That guess is wrong. With churn_risk true and objection_handled false, the first row of the verdict table fires and the server would mark call_008 at risk. This is the reason the verdict is computed by the server from the accepted scores and never taken from the model.
