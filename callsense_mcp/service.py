"""Application logic behind the MCP tools, resources and prompts. No MCP types in here."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import yaml

from . import evals, report, scorers, writeback
from .guard import check_scores, normalize, normalize_quote
from .store import CallSenseError, Store, parse_rules
from .verdict import compute_verdict

SEED_SCORED_BY = "human label (data/graded)"
SEED_SCORED_AT = "2026-09-16T00:00:00+00:00"  # when the graded labels were written
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HOW_TO_SUBMIT = (
    "submit_scores(call_id, rules_version, scores=[{rule_id, value, quote, turn}]). One score per rule. "
    "A true score needs a quote copied word for word from turn n, and that turn must be spoken by the rule's speaker. "
    "A false score takes an empty quote. Nothing is saved until the whole set passes."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CallSense:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------ helpers
    def _entry(self, call: dict[str, Any]) -> dict[str, Any]:
        record = self.store.scores(call["id"])
        verdict = compute_verdict(record["scores"], call["turns"]) if record else None
        return {"call": call, "record": record, "verdict": verdict}

    def _entries(self) -> list[dict[str, Any]]:
        return [self._entry(call) for call in self.store.calls()]

    def _save(self, call: dict[str, Any], rules: dict[str, Any], scores: list[dict[str, Any]], scored_by: str,
              scored_at: str | None = None) -> dict[str, Any]:
        previous = self.store.scores(call["id"])
        record = {
            "call_id": call["id"],
            "rules_version": rules["version"],
            "scored_by": scored_by,
            "scored_at": scored_at or _now(),
            "scores": scores,
        }
        self.store.save_scores(call["id"], record)
        verdict = compute_verdict(scores, call["turns"])
        return {
            "accepted": True,
            "saved": True,
            "call_id": call["id"],
            "rules_version": rules["version"],
            "verdict": verdict["verdict"],
            "reasons": verdict["reasons"],
            "flags": verdict["flags"],
            "replaced_previous": previous is not None,
            "rejected": [],
            "missing": [],
        }

    @staticmethod
    def _refused(call_id: str, rules_version: Any, rejected: list, missing: list, error: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"accepted": False, "saved": False, "call_id": call_id, "rules_version": rules_version}
        if error:
            out["error"] = error
        out["rejected"] = rejected
        out["missing"] = missing
        out["next"] = (
            "Nothing was saved. Fix the rejected rules, add any missing ones, and submit the full set again."
            if not error else "Nothing was saved."
        )
        return out

    # -------------------------------------------------------------- tools
    def list_calls(self, rep: str | None = None, scored: bool | None = None) -> dict[str, Any]:
        rows = []
        for e in self._entries():
            call, record = e["call"], e["record"]
            if rep and call.get("rep", "").lower() != rep.lower():
                continue
            if scored is not None and (record is not None) != scored:
                continue
            rows.append({
                "call_id": call["id"],
                "date": call.get("date"),
                "rep": call.get("rep"),
                "customer": call.get("customer"),
                "turns": len(call["turns"]),
                "scored": record is not None,
                "verdict": e["verdict"]["verdict"] if e["verdict"] else None,
                "rules_version": record["rules_version"] if record else None,
                "scored_by": record.get("scored_by") if record else None,
            })
        return {"count": len(rows), "calls": rows}

    def get_call(self, call_id: str) -> dict[str, Any]:
        call = self.store.call(call_id)
        return {
            "call_id": call["id"],
            "date": call.get("date"),
            "rep": call.get("rep"),
            "customer": call.get("customer"),
            "deal_id": call.get("deal_id"),
            "synthetic": call.get("synthetic", False),
            "turns": [{"n": n, "speaker": t["speaker"], "text": t["text"]} for n, t in enumerate(call["turns"], 1)],
        }

    def get_rules(self, version: int | None = None) -> dict[str, Any]:
        rules = self.store.rules(version)
        return {
            "version": rules["version"],
            "description": rules["description"],
            "quote_required": rules["quote_required"],
            "rules": [{k: r[k] for k in ("id", "question", "evidence", "speaker")} for r in rules["rules"]],
            "available_versions": self.store.rule_versions(),
            "how_to_submit": HOW_TO_SUBMIT,
        }

    def rules_yaml(self, version: int) -> str:
        return self.store.rules_path(version).read_text(encoding="utf-8")

    def submit_scores(self, call_id: str, rules_version: int, scores: list[dict[str, Any]],
                      scored_by: str = "mcp client") -> dict[str, Any]:
        try:
            call = self.store.call(call_id)
        except CallSenseError as exc:
            return self._refused(call_id, rules_version, [], [], error=str(exc))
        if rules_version not in self.store.rule_versions():
            return self._refused(call_id, rules_version, [], [], error=self.store.unknown_version_message(rules_version))
        rules = self.store.rules(rules_version)
        result = check_scores(call, rules, scores)
        if not result.accepted:
            return self._refused(call_id, rules_version, result.rejected, result.missing)
        return self._save(call, rules, result.scores, scored_by)

    def score_call(self, call_id: str, scorer: str = "baseline", rules_version: int | None = None,
                   client: Any = None) -> dict[str, Any]:
        call = self.store.call(call_id)
        rules = self.store.rules(rules_version)
        if scorer == "baseline":
            raw = scorers.score_baseline(rules, call)
            result, log, scored_by = check_scores(call, rules, raw), None, "baseline"
        elif scorer == "claude":
            _, result, log = scorers.score_claude(rules, call, lambda s: check_scores(call, rules, s), client=client)
            scored_by = f"claude:{scorers.model_name()}"
        else:
            raise CallSenseError(f"unknown scorer {scorer!r}; use 'baseline' or 'claude'")
        out = self._save(call, rules, result.scores, scored_by) if result.accepted else \
            self._refused(call_id, rules["version"], result.rejected, result.missing)
        out["scorer"] = scored_by
        if log is not None:
            out["attempts"] = log
        return out

    def team_report(self, rep: str | None = None, since: str | None = None) -> dict[str, Any]:
        if since and not _DATE.match(since):
            raise CallSenseError(f"since must be a date like 2026-09-15, got {since!r}")
        entries = self._entries()
        if rep and not any(e["call"].get("rep", "").lower() == rep.lower() for e in entries):
            reps = sorted({e["call"].get("rep", "?") for e in entries})
            raise CallSenseError(f"no calls for rep {rep!r}; reps: {', '.join(reps)}")
        return report.team_report(entries, rep=rep, since=since)

    def run_eval(self, rules_version: int | None = None, rules_yaml: str | None = None, scorer: str = "baseline",
                 min_agreement: float = 0.8, client: Any = None) -> dict[str, Any]:
        if rules_version is not None and rules_yaml:
            raise CallSenseError("pass rules_version or rules_yaml, not both")
        if not 0.0 <= min_agreement <= 1.0:
            raise CallSenseError("min_agreement is a share between 0 and 1, for example 0.8")
        if rules_yaml:
            try:
                data = yaml.safe_load(rules_yaml)
            except yaml.YAMLError as exc:
                raise CallSenseError(f"rules_yaml is not valid YAML: {exc}") from exc
            rules = parse_rules(data, source="rules_yaml")
            source = f"draft v{rules['version']}, not saved"
        else:
            rules = self.store.rules(rules_version)
            source = f"rules/v{rules['version']}.yaml"
        if scorer == "baseline":
            fn = scorers.score_baseline
            scorer_name = "baseline"
        elif scorer == "claude":
            active = client or scorers.make_client()

            def fn(r: dict[str, Any], c: dict[str, Any]) -> list[dict[str, Any]]:
                raw, _, _ = scorers.score_claude(r, c, lambda s: check_scores(c, r, s), client=active, attempts=1)
                return raw

            scorer_name = f"claude:{scorers.model_name()} (first answer, no retry)"
        else:
            raise CallSenseError(f"unknown scorer {scorer!r}; use 'baseline' or 'claude'")
        graded = self.store.graded()
        if not graded:
            raise CallSenseError("no graded calls in data/graded")
        out = evals.run_eval(rules, graded, fn, min_agreement=min_agreement)
        return {"rules": source, "scorer": scorer_name, **out}

    def deal_card_note(self, call_id: str) -> dict[str, Any]:
        entry = self._entry(self.store.call(call_id))
        if not entry["record"]:
            raise CallSenseError(f"{call_id} is not scored yet; score it with submit_scores or score_call first")
        call, record = entry["call"], entry["record"]
        return {
            "call_id": call["id"],
            "deal_id": call.get("deal_id"),
            "verdict": entry["verdict"]["verdict"],
            "note": writeback.note_text(call, record, entry["verdict"]),
            "deal_update": {"method": "crm.deal.update",
                            "params": writeback.build_payload(call.get("deal_id"), record["scores"], record["rules_version"])},
            "sent": False,
        }

    # ------------------------------------------------------------ prompts
    def coach_prompt(self, rep: str) -> str:
        entries = self._entries()
        mine = [e for e in entries if e["call"].get("rep", "").lower() == rep.lower() and e["record"]]
        if not mine:
            reps = sorted({e["call"].get("rep", "?") for e in entries if e["record"]})
            raise CallSenseError(f"{rep!r} has no scored calls; reps with scored calls: {', '.join(reps)}")
        name = mine[0]["call"]["rep"]
        digest = report.rep_digest(entries, self.store.rules(), name)
        return (
            f"Write this week's coaching note for {name}, a sales rep. Below is every scored call of {name}, "
            f"with each rule's result and the exact quote behind it.\n\n{digest}\n\n"
            "Shape of the note:\n"
            "1. What is working: two or three points, each backed by a quote from the list above with its call id and turn.\n"
            f"2. One skill for this week: the rule {name} misses most often, and what it would sound like in their calls. "
            "To show a missed moment, open the call with get_call and quote it exactly, with the turn number.\n"
            "3. One sentence to try on the next call.\n\n"
            "Quote only text that appears above or that you copied from get_call, word for word. Do not rescore calls. "
            "objection_handled: no can mean the customer raised no objection, so read the call before coaching on it. "
            f"Under 200 words, plain language, addressed to {name}."
        )

    def score_unscored_prompt(self) -> str:
        unscored = [e["call"]["id"] for e in self._entries() if not e["record"]]
        if not unscored:
            return "Every call in callsense is scored. Call team_report() and summarise it in five lines."
        return (
            f"Score every unscored call in callsense. Unscored right now: {', '.join(unscored)}.\n\n"
            "1. get_rules() once for the current rule set.\n"
            "2. For each call: get_call(call_id) and read every turn.\n"
            "3. Decide every rule true or false. For true, copy the evidence word for word from one turn and give that "
            "turn's number n; the turn has to be spoken by the side the rule names in speaker. For false, send an empty quote.\n"
            "4. submit_scores(call_id, rules_version, scores) with one score per rule.\n"
            "5. If the answer says accepted: false, read each rejected reason and hint, fix what it points at, and submit "
            "the full set again. Never paraphrase to get a quote through. If the evidence is not in the call, score the rule false.\n\n"
            "When every call is accepted, call team_report() and summarise it in five lines: calls at risk with their quotes, "
            "calls to follow up, anything still unscored."
        )

    # --------------------------------------------------------------- seed
    def seed_from_labels(self) -> list[str]:
        """Score the graded calls from their human labels, through the guard. Rebuilds the shipped store."""
        rules = self.store.rules()
        known = set(self.store.call_ids())
        seeded = []
        for graded in self.store.graded():
            if graded["id"] not in known:
                continue
            call = self.store.call(graded["id"])
            scores = []
            for rule in rules["rules"]:
                label = graded["labels"].get(rule["id"], {"value": False, "quote": ""})
                q = normalize_quote(label.get("quote", ""))
                hits = [n for n, t in enumerate(call["turns"], 1) if q and q in normalize(t["text"])]
                own = [n for n in hits if call["turns"][n - 1]["speaker"] == rule.get("speaker")]
                turn = (own or hits or [None])[0]
                scores.append({"rule_id": rule["id"], "value": bool(label.get("value")), "quote": label.get("quote", ""), "turn": turn})
            result = check_scores(call, rules, scores)
            if not result.accepted:
                raise CallSenseError(f"labels for {call['id']} do not pass the guard: {result.rejected} {result.missing}")
            self._save(call, rules, result.scores, SEED_SCORED_BY, scored_at=SEED_SCORED_AT)
            seeded.append(call["id"])
        return seeded
