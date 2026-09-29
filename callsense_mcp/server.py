"""The MCP surface: eight tools, two resource templates, two prompts. Logic lives in service.py."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .service import CallSense
from .store import CallSenseError, Store

INSTRUCTIONS = (
    "callsense scores sales calls against the team's playbook. get_rules says what each rule looks for and who has "
    "to say it (rep or customer). Every true score needs a quote copied word for word from one numbered turn of the "
    "call, spoken by that side. submit_scores checks this and saves nothing until the whole set passes. When it "
    "rejects, read reason and hint, fix those rules and send the full set again. Verdicts and flags are computed by "
    "the server from accepted scores, never by the model."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


class Score(BaseModel):
    rule_id: str = Field(description="Rule id from get_rules.")
    value: bool = Field(description="true if the call shows it, false if it does not.")
    quote: str = Field("", description="Text copied word for word from the turn. Required for true, empty for false.")
    turn: int | None = Field(None, description="Number n of the turn the quote comes from, as in get_call. Turns start at 1.")


@contextmanager
def _tool_errors() -> Iterator[None]:
    try:
        yield
    except CallSenseError as exc:
        raise ToolError(str(exc)) from exc


def _client_name(ctx: Context | None) -> str:
    try:
        info = ctx.session.client_params.client_info  # type: ignore[union-attr]
        return f"mcp client: {info.name}" if info and info.name else "mcp client"
    except Exception:
        return "mcp client"


def build_server(home: Path | str | None = None) -> MCPServer:
    app = CallSense(Store(home))
    server = MCPServer("callsense", instructions=INSTRUCTIONS, version=__version__)

    @server.tool(annotations=READ_ONLY)
    def list_calls(rep: str | None = None, scored: bool | None = None) -> dict[str, Any]:
        """List calls with date, rep, customer, number of turns, whether the call is scored, and its verdict.
        Filter by rep name and by scored=true/false."""
        with _tool_errors():
            return app.list_calls(rep=rep, scored=scored)

    @server.tool(annotations=READ_ONLY)
    def get_call(call_id: str) -> dict[str, Any]:
        """One call with numbered turns {n, speaker, text}. Quote from these turns when scoring."""
        with _tool_errors():
            return app.get_call(call_id)

    @server.tool(annotations=READ_ONLY)
    def get_rules(version: int | None = None) -> dict[str, Any]:
        """Scoring rules of a version (latest by default): id, question, what counts as evidence, and speaker,
        the side whose own words the evidence has to be. Also says whether quotes are required."""
        with _tool_errors():
            return app.get_rules(version)

    @server.tool(annotations=WRITES)
    def submit_scores(call_id: str, rules_version: int, scores: list[Score], ctx: Context) -> dict[str, Any]:
        """Submit one score per rule for a call. The server checks every score before saving anything:
        the quote has to be in the turn you name (spaces and case are ignored), that turn has to be spoken by
        the rule's speaker, and with quote_required a true score needs a quote. Every rule of the version has
        to be present. If anything fails you get accepted=false with rejected[{rule_id, reason, hint}] and
        missing[]; nothing is saved, so fix those rules and send the full set again. A false score takes an
        empty quote. On success the server returns the verdict and flags it computed."""
        with _tool_errors():
            return app.submit_scores(call_id, rules_version, [s.model_dump() for s in scores], scored_by=_client_name(ctx))

    @server.tool(annotations=WRITES)
    def score_call(call_id: str, scorer: Literal["baseline", "claude"] = "baseline",
                   rules_version: int | None = None) -> dict[str, Any]:
        """Score a call on the server, for runs with nobody in the loop. 'baseline' is a keyword floor that needs
        no key. 'claude' calls the Anthropic API and needs ANTHROPIC_API_KEY in the server's environment. The
        result goes through the same evidence guard as submit_scores and is saved only if it passes.
        In a chat, prefer get_call plus submit_scores."""
        with _tool_errors():
            return app.score_call(call_id, scorer=scorer, rules_version=rules_version)

    @server.tool(annotations=READ_ONLY)
    def team_report(rep: str | None = None, since: str | None = None) -> dict[str, Any]:
        """Team view over scored calls: rate per rule, a table per rep, calls at risk with the quotes behind them,
        calls to follow up, and unscored calls. Every count lists the call ids it counts.
        Filter by rep and by date with since=YYYY-MM-DD."""
        with _tool_errors():
            return app.team_report(rep=rep, since=since)

    @server.tool(annotations=READ_ONLY)
    def run_eval(rules_version: int | None = None, rules_yaml: str | None = None,
                 scorer: Literal["baseline", "claude"] = "baseline", min_agreement: float = 0.8) -> dict[str, Any]:
        """Measure a rule set against the hand-graded calls before it ships: agreement with human labels per
        rule, quote validity, and pass or fail. Every rule and the total have to reach min_agreement. Pass
        rules_yaml to test a draft rule set without saving it. A rule with no human labels cannot pass."""
        with _tool_errors():
            return app.run_eval(rules_version=rules_version, rules_yaml=rules_yaml, scorer=scorer, min_agreement=min_agreement)

    @server.tool(annotations=READ_ONLY)
    def deal_card_note(call_id: str) -> dict[str, Any]:
        """The note the CRM write-back would put on the deal card: verdict, flags, every score with its quote,
        and the deal-field payload. Builds the text only; nothing is sent."""
        with _tool_errors():
            return app.deal_card_note(call_id)

    @server.resource("callsense://rules/{version}", mime_type="application/yaml",
                     description="A rule set as YAML, the file a rule change is a diff of. version: 1, 2, ...")
    def rules_resource(version: str) -> str:
        number = version.lstrip("vV")
        if not number.isdigit() or int(number) not in app.store.rule_versions():
            raise ResourceNotFoundError(f"no rules version {version}")
        return app.rules_yaml(int(number))

    @server.resource("callsense://calls/{call_id}", mime_type="application/json",
                     description="One call with metadata and numbered turns.")
    def call_resource(call_id: str) -> str:
        try:
            return json.dumps(app.get_call(call_id), indent=2, ensure_ascii=False)
        except CallSenseError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    @server.prompt(description="Weekly coaching note for one rep, from their scored calls and the quotes behind each score.")
    def coach_rep(rep: str) -> str:
        try:
            return app.coach_prompt(rep)
        except CallSenseError as exc:  # a protocol error keeps its message; anything else is reported generically
            raise MCPError(INVALID_PARAMS, str(exc)) from exc

    @server.prompt(description="Score every unscored call with get_rules, get_call and submit_scores, fixing rejected quotes, then run team_report.")
    def score_unscored() -> str:
        return app.score_unscored_prompt()

    return server


def serve() -> None:
    build_server().run()
