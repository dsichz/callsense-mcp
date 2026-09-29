"""The evidence guard. Every score set passes through here before anything is saved.

Ported from `quote_is_valid` in call-scoring-harness and made stricter. A quote has to be in the
turn the scorer names, and a positive score's quote has to be said by the side the rule names:
churn risk is the customer's words, handling an objection is the rep's. A rejected score comes back
with a reason and a hint the model can act on: the turn where the quote really is, or the closest
sentence in the call. Nothing is saved unless the whole set passes.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

MIN_QUOTE_WORDS = 3  # a shorter quote passes only when it is a whole sentence of its turn
CLOSE_MATCH = 0.6  # difflib ratio from which the closest sentence is offered as a hint

_TYPOGRAPHY = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " "}
)
_WRAPPERS = " \"'\u00ab\u00bb"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace, fold case, make quotes and dashes plain. Keep a map back to the source."""
    chars: list[str] = []
    index: list[int] = []
    pending_space = False
    for i, ch in enumerate((text or "").translate(_TYPOGRAPHY)):
        if ch.isspace():
            pending_space = bool(chars)
            continue
        if pending_space:
            chars.append(" ")
            index.append(i - 1)
            pending_space = False
        for low in ch.lower():
            chars.append(low)
            index.append(i)
    return "".join(chars), index


def normalize(text: str) -> str:
    return _normalized_with_map(text)[0]


def normalize_quote(quote: str) -> str:
    """normalize() plus the quotation marks a model may wrap around a quote."""
    return normalize(quote).strip(_WRAPPERS)


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split((text or "").strip()) if s]


def verbatim(text: str, quote: str) -> str:
    """The exact span of `text` that `quote` matches after normalisation, so the stored quote is the call's own wording."""
    norm, index = _normalized_with_map(text)
    q = normalize_quote(quote)
    pos = norm.find(q) if q else -1
    if pos < 0:
        return quote.strip()
    return text[index[pos] : index[pos + len(q) - 1] + 1]


@dataclass
class GuardResult:
    accepted: bool
    scores: list[dict[str, Any]] = field(default_factory=list)  # cleaned scores, filled only when accepted
    rejected: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "rejected": self.rejected, "missing": self.missing}


def _reject(rule_id: Any, reason: str, hint: str) -> dict[str, Any]:
    return {"rule_id": rule_id, "reason": reason, "hint": hint}


def _turns_label(numbers: list[int]) -> str:
    return f"turn {numbers[0]}" if len(numbers) == 1 else "turns " + ", ".join(str(n) for n in numbers)


def _where_found(hits: list[int], turns: list[dict], rule: dict, value: bool) -> str:
    speaker = rule.get("speaker")
    if value and speaker:
        right = [n for n in hits if turns[n - 1]["speaker"] == speaker]
        if right:
            return f"found in {_turns_label(right)}"
        who = turns[hits[0] - 1]["speaker"]
        return f"found in {_turns_label(hits)}, but that is the {who} speaking; {rule['id']} needs the {speaker}'s own words"
    return f"found in {_turns_label(hits)}"


def _closest(quote_norm: str, turns: list[dict]) -> tuple[float, int, str]:
    best = (0.0, 0, "")
    for n, turn in enumerate(turns, 1):
        parts = sentences(turn["text"])
        for candidate in parts + ([turn["text"]] if len(parts) > 1 else []):
            ratio = difflib.SequenceMatcher(None, quote_norm, normalize(candidate), autojunk=False).ratio()
            if ratio > best[0]:
                best = (ratio, n, candidate)
    return best


def _not_found_hint(quote_norm: str, turns: list[dict], rule: dict, value: bool) -> str:
    ratio, n, text = _closest(quote_norm, turns)
    if ratio < CLOSE_MATCH:
        return f"nothing in the call is close to this quote. Quote the call verbatim, or score {rule['id']} false."
    who = turns[n - 1]["speaker"]
    hint = f'closest sentence is in turn {n} ({who}): "{text}" Copy it exactly, or score {rule["id"]} false.'
    speaker = rule.get("speaker")
    if value and speaker and who != speaker:
        hint += f" Note that turn {n} is the {who}; {rule['id']} needs the {speaker}'s own words."
    return hint


def _check_one(rule: dict, value: bool, quote: str, turn: Any, turns: list[dict], quote_required: bool) -> dict | None:
    rid = rule["id"]
    q = normalize_quote(quote)
    if not q:
        if value and quote_required:
            return _reject(
                rid,
                "true score without a quote",
                "copy the sentence from the call that shows it and give its turn number, or score it false",
            )
        return None

    hits = [n for n, t in enumerate(turns, 1) if q in normalize(t["text"])]
    if not (type(turn) is int and 1 <= turn <= len(turns)):
        reason = "no turn number" if turn is None else f"turn {turn} does not exist; this call has turns 1 to {len(turns)}"
        hint = _where_found(hits, turns, rule, value) if hits else _not_found_hint(q, turns, rule, value)
        return _reject(rid, reason, hint)
    if turn not in hits:
        if hits:
            return _reject(rid, f"quote is not in turn {turn}", _where_found(hits, turns, rule, value))
        return _reject(rid, "quote not found anywhere in the call", _not_found_hint(q, turns, rule, value))
    if not value:
        return None  # a false score may carry context; the quote only has to be real

    speaker = rule.get("speaker")
    said_by = turns[turn - 1]["speaker"]
    if speaker and said_by != speaker:
        own = [n for n, t in enumerate(turns, 1) if t["speaker"] == speaker]
        return _reject(
            rid,
            f"turn {turn} is the {said_by} speaking; {rid} needs the {speaker}'s own words",
            f"{speaker} turns in this call: {', '.join(str(n) for n in own)}",
        )
    text = turns[turn - 1]["text"]
    whole = {normalize_quote(s) for s in sentences(text)} | {normalize_quote(text)}
    if len(q.split()) < MIN_QUOTE_WORDS and q not in whole:
        sentence = next((s for s in sentences(text) if q in normalize(s)), text)
        return _reject(rid, "quote too short to stand as evidence", f'quote the whole sentence from turn {turn}: "{sentence}"')
    return None


def check_scores(call: dict[str, Any], rules: dict[str, Any], scores: list[Any]) -> GuardResult:
    """Check a full score set for one call against one rule version.

    Each score is {rule_id, value, quote, turn}, turns numbered from 1. Returns the cleaned scores only
    when every rule of the version is covered and every score passes.
    """
    turns = call["turns"]
    by_id = {r["id"]: r for r in rules["rules"]}
    version = rules.get("version")
    quote_required = bool(rules.get("quote_required"))
    rejected: list[dict[str, Any]] = []
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in scores or []:
        s = raw if isinstance(raw, dict) else {}
        rid = s.get("rule_id")
        if rid not in by_id:
            rejected.append(_reject(rid, f"no rule {rid!r} in rules v{version}", f"rules v{version} has: {', '.join(by_id)}"))
            continue
        if rid in seen:
            rejected.append(_reject(rid, "rule scored twice", "send exactly one score per rule"))
            continue
        seen.add(rid)
        value = s.get("value")
        if not isinstance(value, bool):
            rejected.append(_reject(rid, "value must be true or false", "send value as a boolean"))
            continue
        quote = str(s.get("quote") or "")
        turn = s.get("turn")
        problem = _check_one(by_id[rid], value, quote, turn, turns, quote_required)
        if problem:
            rejected.append(problem)
            continue
        if normalize_quote(quote):
            clean.append({"rule_id": rid, "value": value, "quote": verbatim(turns[turn - 1]["text"], quote), "turn": turn})
        else:
            clean.append({"rule_id": rid, "value": value, "quote": "", "turn": None})

    missing = [rid for rid in by_id if rid not in seen]
    accepted = not rejected and not missing
    order = list(by_id)
    clean.sort(key=lambda item: order.index(item["rule_id"]))
    return GuardResult(accepted=accepted, scores=clean if accepted else [], rejected=rejected, missing=missing)
