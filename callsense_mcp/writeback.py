"""The deal-card note. Same payload shape as writeback_example.py in call-scoring-harness (Bitrix24 REST).

In production the flags land on the deal card the reps already look at. Here the payload and the
note text are only built and returned. Nothing is sent anywhere.
"""
from __future__ import annotations

from typing import Any

FIELD_MAP = {  # rule id -> custom field on the deal, from call-scoring-harness
    "next_step_committed": "UF_CRM_NEXT_STEP",
    "objection_handled": "UF_CRM_OBJECTION",
    "qualification_asked": "UF_CRM_QUALIFIED",
    "churn_risk": "UF_CRM_CHURN_RISK",
}


def build_payload(deal_id: Any, scores: list[dict[str, Any]], rules_version: int) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for s in scores:
        field = FIELD_MAP.get(s["rule_id"])
        if not field:
            continue
        # value and quote travel together; the quote is what the rep sees on the card
        fields[field] = "yes" if s["value"] else "no"
        fields[field + "_QUOTE"] = s["quote"]
    fields["UF_CRM_SCORING_RULES_VERSION"] = str(rules_version)
    return {"id": deal_id, "fields": fields}


def _quoted(evidence: dict[str, Any] | None) -> str:
    if not evidence:
        return ""
    return f'{evidence["speaker"]}, turn {evidence["turn"]}: "{evidence["quote"]}"'


def note_text(call: dict[str, Any], record: dict[str, Any], verdict: dict[str, Any]) -> str:
    turns = call["turns"]
    lines = [
        f"callsense · {call['id']} · {call.get('date')} · {call.get('rep')} with {call.get('customer')}",
        f"Verdict: {verdict['verdict'].upper()}.",
    ]
    for reason in verdict["reasons"]:
        lines.append(f"  {reason['reason'].capitalize()}.")
        if reason.get("evidence"):
            lines.append(f"  Evidence, {_quoted(reason['evidence'])}")
    lines.append("Flags: " + (" · ".join(f["flag"] for f in verdict["flags"]) if verdict["flags"] else "none"))
    lines.append(f"Scores, rules v{record['rules_version']}:")
    width = max(len(s["rule_id"]) for s in record["scores"])
    for s in record["scores"]:
        row = f"  {s['rule_id']:<{width}}  {'yes' if s['value'] else 'no '}"
        if s["quote"]:
            row += f"  {turns[s['turn'] - 1]['speaker']}, turn {s['turn']}: \"{s['quote']}\""
        lines.append(row)
    lines.append(
        f"Scored by {record.get('scored_by')} on {str(record.get('scored_at', ''))[:10]}. "
        "Every quote above is copied from this call and was checked before it was saved."
    )
    return "\n".join(lines)
