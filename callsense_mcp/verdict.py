"""What a scored call means for the business. All interpretation rules live in this file.

The model answers the rule questions. The verdict and the flags are computed here, from the accepted
scores only, so the same scores always give the same verdict and every verdict can be explained with
the rows below.
"""
from __future__ import annotations

from typing import Any

AT_RISK = "at risk"
FOLLOW_UP = "follow up"
HEALTHY = "healthy"
SEVERITY = {HEALTHY: 0, FOLLOW_UP: 1, AT_RISK: 2}

# Checked top to bottom. A row fires when every listed rule has the listed value.
# The most severe row that fires sets the verdict; its reasons are reported with their evidence.
VERDICT_RULES: list[dict[str, Any]] = [
    {
        "verdict": AT_RISK,
        "when": {"churn_risk": True, "objection_handled": False},
        "reason": "customer signalled leaving and the objection was not answered",
        "evidence": "churn_risk",
    },
    {
        "verdict": AT_RISK,
        "when": {"churn_risk": True, "next_step_committed": False},
        "reason": "customer signalled leaving and no next step was agreed",
        "evidence": "churn_risk",
    },
    {
        "verdict": FOLLOW_UP,
        "when": {"next_step_committed": False},
        "reason": "no next step agreed",
        "evidence": None,
    },
]

# Flags are shown on the deal card and in the report whatever the verdict is.
FLAGS: list[dict[str, Any]] = [
    {"flag": "churn signal", "rule": "churn_risk", "value": True},
    {"flag": "no next step", "rule": "next_step_committed", "value": False},
    {"flag": "not qualified", "rule": "qualification_asked", "value": False},
]

# For the team report: a "yes" on these rules is a customer signal, not a rep skill.
SIGNAL_RULES = {"churn_risk"}
CAVEATS = {"objection_handled": "a 'no' also covers calls where the customer raised no objection"}


def _evidence(score: dict[str, Any] | None, turns: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not score or not score.get("quote"):
        return None
    turn = score.get("turn")
    speaker = turns[turn - 1]["speaker"] if turns and isinstance(turn, int) and 0 < turn <= len(turns) else None
    return {"rule_id": score["rule_id"], "turn": turn, "speaker": speaker, "quote": score["quote"]}


def compute_verdict(scores: list[dict[str, Any]], turns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    by_rule = {s["rule_id"]: s for s in scores}
    fired = [
        row
        for row in VERDICT_RULES
        if all(rule in by_rule and by_rule[rule]["value"] is value for rule, value in row["when"].items())
    ]
    verdict = max((row["verdict"] for row in fired), key=SEVERITY.__getitem__, default=HEALTHY)
    reasons = [
        {"reason": row["reason"], "evidence": _evidence(by_rule.get(row["evidence"]), turns) if row["evidence"] else None}
        for row in fired
        if row["verdict"] == verdict
    ]
    flags = []
    for row in FLAGS:
        score = by_rule.get(row["rule"])
        if score is not None and score["value"] is row["value"]:
            flags.append({"flag": row["flag"], "rule_id": row["rule"], "evidence": _evidence(score, turns)})
    return {"verdict": verdict, "reasons": reasons, "flags": flags}
