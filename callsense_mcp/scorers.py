"""Server-side scorers for runs with no person in the loop.

baseline  keyword heuristics copied from call-scoring-harness. No key needed. A floor and a smoke test.
claude    Anthropic Messages API with a fixed JSON schema. Needs ANTHROPIC_API_KEY on the server.

Whatever a scorer returns goes through the same evidence guard as submit_scores. The claude scorer
gets the guard's rejections back once and may correct itself, the same loop an MCP client runs.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from .guard import GuardResult
from .store import CallSenseError

DEFAULT_MODEL = "claude-sonnet-5"  # same default as call-scoring-harness; override with SCORER_MODEL
MAX_ATTEMPTS = 2

# rule id -> speaker and keywords, from call-scoring-harness scorer.py.
# A rule in YAML may override both with its own `speaker` and `keywords`.
KEYWORDS: dict[str, dict[str, Any]] = {
    "next_step_committed": {
        "speaker": "customer",
        "any": ["monday", "tuesday", "wednesday", "thursday", "friday", "tomorrow", "next week",
                "send me", "let's do", "book", "i'll sign", "deal", "ok, do it", "agreed"],
    },
    "objection_handled": {
        "speaker": "rep",
        "any": ["price", "cost", "timing", "compared", "difference", "option", "what if", "the reason"],
    },
    "qualification_asked": {
        "speaker": "rep",
        "any": ["who decides", "who signs", "who approves", "budget", "when do you need", "deadline", "timeline"],
    },
    "churn_risk": {
        "speaker": "customer",
        "any": ["competitor", "another supplier", "cheaper", "pause", "not happy", "disappointed",
                "switch", "quote from", "other offers"],
    },
}


def score_baseline(rules: dict[str, Any], call: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rule in rules["rules"]:
        spec = KEYWORDS.get(rule["id"], {})
        words = rule.get("keywords") or spec.get("any", [])
        speaker = rule.get("speaker") or spec.get("speaker")
        hit = None
        for n, turn in enumerate(call["turns"], 1):
            if speaker and turn["speaker"] != speaker:
                continue
            if any(word in turn["text"].lower() for word in words):
                hit = (n, turn["text"])
                break
        out.append({"rule_id": rule["id"], "value": hit is not None, "quote": hit[1] if hit else "", "turn": hit[0] if hit else None})
    return out


# ------------------------------------------------------------------ claude
SYSTEM = (
    "You score B2B sales calls against a fixed playbook. For every rule answer true or false. "
    "For a true answer copy the evidence verbatim from one turn, without edits, and give that turn's number. "
    "The turn must be spoken by the side the rule names. If there is no such evidence, the answer is false, "
    "the quote is an empty string and the turn is 0. Never paraphrase, shorten with ellipses, or join text from two turns."
)


def model_name() -> str:
    return os.environ.get("SCORER_MODEL", DEFAULT_MODEL)


def make_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise CallSenseError(
            "scorer 'claude' needs ANTHROPIC_API_KEY in the server's environment. Without a key use "
            "scorer='baseline', or score the call from your MCP client with get_call and submit_scores."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise CallSenseError("scorer 'claude' needs the anthropic package: pip install 'callsense-mcp[claude]'") from exc
    return anthropic.Anthropic()


def _schema(rules: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string", "enum": [r["id"] for r in rules["rules"]]},
                        "value": {"type": "boolean"},
                        "quote": {"type": "string"},
                        "turn": {"type": "integer"},
                    },
                    "required": ["rule_id", "value", "quote", "turn"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["scores"],
        "additionalProperties": False,
    }


def _prompt(rules: dict[str, Any], call: dict[str, Any]) -> str:
    rule_lines = "\n".join(
        f"- {r['id']} (said by: {r.get('speaker') or 'either side'}): {r['question']} Evidence: {r['evidence']}"
        for r in rules["rules"]
    )
    turns = "\n".join(f"[{n}] {t['speaker']}: {t['text']}" for n, t in enumerate(call["turns"], 1))
    return f"Rules (version {rules['version']}):\n{rule_lines}\n\nCall {call['id']}, turns numbered:\n{turns}"


def _feedback(result: GuardResult) -> str:
    lines = ["The server rejected this score set. Nothing was saved."]
    lines += [f"- {r['rule_id']}: {r['reason']}. Hint: {r['hint']}" for r in result.rejected]
    if result.missing:
        lines.append(f"- missing rules: {', '.join(result.missing)}")
    lines.append("Return the full corrected set for every rule.")
    return "\n".join(lines)


def score_claude(
    rules: dict[str, Any],
    call: dict[str, Any],
    check: Callable[[list[dict[str, Any]]], GuardResult],
    client: Any = None,
    attempts: int = MAX_ATTEMPTS,
) -> tuple[list[dict[str, Any]], GuardResult, list[dict[str, Any]]]:
    """Ask Claude for a score set, run it through `check`, feed rejections back.

    Returns the last raw score set, the guard's verdict on it, and a log of every attempt.
    """
    client = client or make_client()
    model = model_name()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _prompt(rules, call)}]
    log: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    result = GuardResult(accepted=False)
    for attempt in range(1, attempts + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": _schema(rules)}},
            )
        except Exception as exc:  # auth, rate limit, network: report the SDK's own message
            raise CallSenseError(f"Anthropic API call failed: {type(exc).__name__}: {exc}") from exc
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise CallSenseError(f"model stopped with stop_reason={resp.stop_reason}; nothing was saved")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            scores = json.loads(text)["scores"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CallSenseError("model returned output that is not the expected JSON; nothing was saved") from exc
        result = check(scores)
        log.append({"attempt": attempt, "model": model, **result.as_dict()})
        if result.accepted:
            break
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": _feedback(result)}]
    return scores, result, log
