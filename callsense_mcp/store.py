"""File store: rules, calls, hand-graded labels and accepted scores.

Layout under CALLSENSE_HOME (the repository root by default):

    rules/v{N}.yaml        versioned scoring rules
    data/calls/*.json      calls with metadata (date, rep, customer, deal_id) and turns
    data/graded/*.json     the hand-graded eval set (turns + human labels)
    data/scores/*.json     accepted score sets, one file per call, written only by the guard path
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

SPEAKERS = ("rep", "customer")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RULES_FILE = re.compile(r"^v(\d+)\.yaml$")


class CallSenseError(Exception):
    """An anticipated failure whose message is safe and useful to show to the caller."""


def default_home() -> Path:
    env = os.environ.get("CALLSENSE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def parse_rules(data: Any, source: str = "rules") -> dict[str, Any]:
    """Validate a rule set loaded from YAML and return it in a normalised shape."""
    if not isinstance(data, dict):
        raise CallSenseError(f"{source}: expected a mapping with version and rules")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise CallSenseError(f"{source}: version must be an integer")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise CallSenseError(f"{source}: rules must be a non-empty list")
    out, seen = [], set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"].strip():
            raise CallSenseError(f"{source}: rule #{i + 1} needs an id")
        rid = rule["id"].strip()
        if rid in seen:
            raise CallSenseError(f"{source}: rule {rid} is defined twice")
        seen.add(rid)
        speaker = rule.get("speaker")
        if speaker is not None and speaker not in SPEAKERS:
            raise CallSenseError(f"{source}: rule {rid} has speaker {speaker!r}, expected rep or customer")
        keywords = rule.get("keywords")
        if keywords is not None and not (isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)):
            raise CallSenseError(f"{source}: rule {rid} keywords must be a list of strings")
        item = {
            "id": rid,
            "question": str(rule.get("question", "")).strip(),
            "evidence": str(rule.get("evidence", "")).strip(),
            "speaker": speaker,
        }
        if keywords:
            item["keywords"] = [k.lower() for k in keywords]
        out.append(item)
    return {
        "version": version,
        "description": str(data.get("description", "")).strip(),
        "quote_required": bool(data.get("quote_required", False)),
        "rules": out,
    }


class Store:
    def __init__(self, home: Path | str | None = None):
        self.home = Path(home).resolve() if home else default_home()
        self.rules_dir = self.home / "rules"
        self.calls_dir = self.home / "data" / "calls"
        self.graded_dir = self.home / "data" / "graded"
        self.scores_dir = self.home / "data" / "scores"
        if not self.rules_dir.is_dir() or not self.calls_dir.is_dir():
            raise CallSenseError(
                f"{self.home} has no rules/ and data/calls/. Point CALLSENSE_HOME at a callsense-mcp data folder."
            )

    # ------------------------------------------------------------------ rules
    def rule_versions(self) -> list[int]:
        found = (_RULES_FILE.match(p.name) for p in self.rules_dir.iterdir())
        return sorted(int(m.group(1)) for m in found if m)

    def rules_path(self, version: int) -> Path:
        if version not in self.rule_versions():
            raise CallSenseError(self.unknown_version_message(version))
        return self.rules_dir / f"v{version}.yaml"

    def unknown_version_message(self, version: Any) -> str:
        known = ", ".join(str(v) for v in self.rule_versions())
        return f"unknown rules version {version}; available: {known}"

    def rules(self, version: int | None = None) -> dict[str, Any]:
        if version is None:
            versions = self.rule_versions()
            if not versions:
                raise CallSenseError("no rule sets in rules/")
            version = versions[-1]
        path = self.rules_path(version)
        rules = parse_rules(yaml.safe_load(path.read_text(encoding="utf-8")), source=path.name)
        if rules["version"] != version:
            raise CallSenseError(f"{path.name} declares version {rules['version']}")
        return rules

    # ------------------------------------------------------------------ calls
    def _check_id(self, call_id: str) -> str:
        if not isinstance(call_id, str) or not _ID.match(call_id):
            raise CallSenseError(f"invalid call id {call_id!r}")
        return call_id

    def call_ids(self) -> list[str]:
        return sorted(p.stem for p in self.calls_dir.glob("*.json"))

    def call(self, call_id: str) -> dict[str, Any]:
        path = self.calls_dir / f"{self._check_id(call_id)}.json"
        if not path.is_file():
            raise CallSenseError(f"no call {call_id}; known calls: {', '.join(self.call_ids())}")
        return json.loads(path.read_text(encoding="utf-8"))

    def calls(self) -> list[dict[str, Any]]:
        return [self.call(cid) for cid in self.call_ids()]

    def graded(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.graded_dir.glob("*.json"))]

    # ----------------------------------------------------------------- scores
    def scores(self, call_id: str) -> dict[str, Any] | None:
        path = self.scores_dir / f"{self._check_id(call_id)}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_scores(self, call_id: str, record: dict[str, Any]) -> Path:
        """Write one accepted score set. Only the guard path calls this."""
        self.scores_dir.mkdir(parents=True, exist_ok=True)
        path = self.scores_dir / f"{self._check_id(call_id)}.json"
        fd, tmp = tempfile.mkstemp(dir=self.scores_dir, prefix=f".{call_id}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        return path
