"""Team report and the per-rep digest behind the coach_rep prompt.

Every count in the report comes with the call ids it counts, so a sales director can open any
number and land on the calls and quotes behind it.
"""
from __future__ import annotations

from typing import Any

from .verdict import AT_RISK, CAVEATS, FOLLOW_UP, HEALTHY, SIGNAL_RULES


def _value(entry: dict[str, Any], rule_id: str) -> bool | None:
    for s in entry["record"]["scores"]:
        if s["rule_id"] == rule_id:
            return s["value"]
    return None


def _rule_ids(entries: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for e in entries:
        for s in e["record"]["scores"]:
            if s["rule_id"] not in ids:
                ids.append(s["rule_id"])
    return ids


def _in_scope(entry: dict[str, Any], rep: str | None, since: str | None) -> bool:
    call = entry["call"]
    if rep and call.get("rep", "").lower() != rep.lower():
        return False
    if since and call.get("date", "") < since:
        return False
    return True


def team_report(entries: list[dict[str, Any]], rep: str | None = None, since: str | None = None) -> dict[str, Any]:
    """entries: [{call, record (accepted score set or None), verdict (or None)}] for every call in the store."""
    scope = [e for e in entries if _in_scope(e, rep, since)]
    scored = [e for e in scope if e["record"]]
    unscored = [e["call"]["id"] for e in scope if not e["record"]]

    rules = []
    for rid in _rule_ids(scored):
        yes = [e["call"]["id"] for e in scored if _value(e, rid) is True]
        no = [e["call"]["id"] for e in scored if _value(e, rid) is False]
        row = {
            "rule_id": rid,
            "reads_as": "signal rate (yes = customer signal)" if rid in SIGNAL_RULES else "pass rate",
            "yes": len(yes),
            "of": len(yes) + len(no),
            "rate": round(len(yes) / (len(yes) + len(no)), 2) if yes or no else None,
            "yes_calls": yes,
            "no_calls": no,
        }
        if rid in CAVEATS:
            row["caveat"] = CAVEATS[rid]
        rules.append(row)

    by_rep = []
    for name in sorted({e["call"].get("rep", "?") for e in scope}):
        mine = [e for e in scope if e["call"].get("rep", "?") == name]
        mine_scored = [e for e in mine if e["record"]]
        by_rep.append({
            "rep": name,
            "calls": len(mine),
            "scored_calls": [e["call"]["id"] for e in mine_scored],
            "unscored_calls": [e["call"]["id"] for e in mine if not e["record"]],
            "verdicts": {v: [e["call"]["id"] for e in mine_scored if e["verdict"]["verdict"] == v] for v in (AT_RISK, FOLLOW_UP, HEALTHY)},
            "rules": {
                rid: {"yes": sum(_value(e, rid) is True for e in mine_scored),
                      "of": sum(_value(e, rid) is not None for e in mine_scored),
                      "yes_calls": [e["call"]["id"] for e in mine_scored if _value(e, rid) is True]}
                for rid in _rule_ids(mine_scored)
            },
        })

    def listing(verdict: str) -> list[dict[str, Any]]:
        return [
            {"call_id": e["call"]["id"], "date": e["call"].get("date"), "rep": e["call"].get("rep"),
             "customer": e["call"].get("customer"), "reasons": e["verdict"]["reasons"]}
            for e in scored if e["verdict"]["verdict"] == verdict
        ]

    at_risk, follow_up = listing(AT_RISK), listing(FOLLOW_UP)
    healthy = [e["call"]["id"] for e in scored if e["verdict"]["verdict"] == HEALTHY]
    scored_by: dict[str, list[str]] = {}
    versions: dict[str, list[str]] = {}
    for e in scored:
        scored_by.setdefault(e["record"].get("scored_by", "?"), []).append(e["call"]["id"])
        versions.setdefault(f"v{e['record']['rules_version']}", []).append(e["call"]["id"])

    def ids(items: list[Any]) -> str:
        return f" ({', '.join(items)})" if items else ""

    summary = (
        f"{len(scored)} of {len(scope)} calls scored. At risk: {len(at_risk)}{ids([a['call_id'] for a in at_risk])}. "
        f"Follow up: {len(follow_up)}{ids([f['call_id'] for f in follow_up])}. Healthy: {len(healthy)}{ids(healthy)}. "
        f"Unscored: {len(unscored)}{ids(unscored)}."
    )
    return {
        "filters": {"rep": rep, "since": since},
        "summary": summary,
        "calls_in_scope": [e["call"]["id"] for e in scope],
        "scored": {"count": len(scored), "call_ids": [e["call"]["id"] for e in scored]},
        "unscored": {"count": len(unscored), "call_ids": unscored},
        "rules": rules,
        "by_rep": by_rep,
        "at_risk": at_risk,
        "follow_up": follow_up,
        "healthy": healthy,
        "scored_by": scored_by,
        "rules_versions": versions,
    }


def rep_digest(entries: list[dict[str, Any]], rules: dict[str, Any], rep: str) -> str:
    """Plain-text digest of one rep's scored calls, embedded in the coach_rep prompt."""
    mine = [e for e in entries if e["call"].get("rep", "").lower() == rep.lower() and e["record"]]
    lines = [f"Rules v{rules['version']}:"]
    lines += [f"- {r['id']}: {r['question']}" for r in rules["rules"]]
    for e in sorted(mine, key=lambda x: x["call"].get("date", "")):
        call, record, verdict = e["call"], e["record"], e["verdict"]
        lines.append("")
        lines.append(f"{call['id']} · {call.get('date')} · {call.get('customer')} · verdict: {verdict['verdict']} "
                     f"(rules v{record['rules_version']}, scored by {record.get('scored_by')})")
        turns = call["turns"]
        for s in record["scores"]:
            if s["quote"]:
                who = turns[s["turn"] - 1]["speaker"]
                lines.append(f"  {s['rule_id']}: {'yes' if s['value'] else 'no'} · {who}, turn {s['turn']}: \"{s['quote']}\"")
            else:
                lines.append(f"  {s['rule_id']}: {'yes' if s['value'] else 'no'}")
    return "\n".join(lines)
