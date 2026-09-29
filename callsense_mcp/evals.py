"""Measure a rule set against the hand-graded calls. Ported from call-scoring-harness evaluate.py.

A score counts as agreeing when its value equals the human label. A positive score whose quote
fails the evidence guard counts as false when the rule set requires quotes, the same way the
harness treated an invented quote. The gate applies to every rule and to the total. A rule with
no human labels cannot pass: it has not been measured.
"""
from __future__ import annotations

from typing import Any, Callable

from .guard import check_scores


def run_eval(
    rules: dict[str, Any],
    graded: list[dict[str, Any]],
    scorer: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]],
    min_agreement: float = 0.8,
) -> dict[str, Any]:
    quote_required = bool(rules.get("quote_required"))
    stats = {r["id"]: {"labeled": 0, "agree": 0, "positives": 0, "quote_valid": 0, "disagree": []} for r in rules["rules"]}
    misses: list[dict[str, Any]] = []

    for call in graded:
        scores = scorer(rules, call)
        by_rule = {s["rule_id"]: s for s in scores}
        rejected = {r["rule_id"]: r for r in check_scores(call, rules, scores).rejected}
        labels = call.get("labels", {})
        for rule in rules["rules"]:
            rid = rule["id"]
            if rid not in labels:
                continue
            st = stats[rid]
            st["labeled"] += 1
            score = by_rule.get(rid, {"value": False, "quote": ""})
            said = bool(score.get("value"))
            quote_ok = rid not in rejected
            if said:
                st["positives"] += 1
                st["quote_valid"] += quote_ok
            counted = said and (quote_ok or not quote_required)
            human = bool(labels[rid].get("value"))
            if counted == human:
                st["agree"] += 1
            else:
                st["disagree"].append(call["id"])
            if counted != human or (said and not quote_ok):
                why = f"invalid quote: {rejected[rid]['reason']}" if said and not quote_ok else "value"
                misses.append({"call_id": call["id"], "rule_id": rid, "human": human, "model": said, "why": why,
                               "quote": str(score.get("quote", ""))[:160]})

    per_rule, total_labeled, total_agree = [], 0, 0
    for rid, st in stats.items():
        per_rule.append({
            "rule_id": rid,
            "agreement": round(st["agree"] / st["labeled"], 3) if st["labeled"] else None,
            "agree": st["agree"],
            "labeled": st["labeled"],
            "quote_valid": f"{st['quote_valid']}/{st['positives']}" if st["positives"] else "-",
            "disagree_calls": st["disagree"],
        })
        total_labeled += st["labeled"]
        total_agree += st["agree"]
    unlabeled = [rid for rid, st in stats.items() if not st["labeled"]]
    failing = [row["rule_id"] for row in per_rule if row["agreement"] is not None and row["agreement"] < min_agreement]
    overall = total_agree / total_labeled if total_labeled else 0.0
    # The gate holds per rule and overall: a strong rule set must not carry one rule that is wrong half the time.
    passed = bool(total_labeled) and not unlabeled and not failing and overall >= min_agreement
    if passed:
        verdict = "pass: every rule is at or above the gate"
    elif unlabeled:
        verdict = f"fail: no human labels for {', '.join(unlabeled)}; label calls in data/graded before this rule can ship"
    else:
        below = f" ({', '.join(failing)})" if failing else ""
        verdict = f"fail: agreement below the gate{below}; do not ship this rule set"
    return {
        "graded_calls": [c["id"] for c in graded],
        "per_rule": per_rule,
        "overall_agreement": round(overall, 3),
        "gate": min_agreement,
        "passed": passed,
        "verdict": verdict,
        "failing_rules": failing,
        "unlabeled_rules": unlabeled,
        "misses": misses,
    }
