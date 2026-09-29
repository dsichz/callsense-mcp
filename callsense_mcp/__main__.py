"""Command line.

    python -m callsense_mcp                 run the MCP server on stdio (what Claude Code and Claude Desktop start)
    python -m callsense_mcp eval [...]      run the eval gate, exit 1 when it fails (for CI)
    python -m callsense_mcp seed            rebuild data/scores for the graded calls from their human labels
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .service import CallSense
from .store import CallSenseError, Store


def _eval(args: argparse.Namespace) -> int:
    app = CallSense(Store())
    rules_yaml = Path(args.rules_file).read_text(encoding="utf-8") if args.rules_file else None
    out = app.run_eval(rules_version=args.rules_version, rules_yaml=rules_yaml, scorer=args.scorer,
                       min_agreement=args.min_agreement)
    print(f"{out['rules']} · scorer {out['scorer']} · {len(out['graded_calls'])} graded calls\n")
    print(f"{'rule':24} {'agree':>7} {'quote ok':>9}")
    for row in out["per_rule"]:
        agree = f"{row['agreement']:.0%}" if row["agreement"] is not None else "no labels"
        print(f"{row['rule_id']:24} {agree:>7} {row['quote_valid']:>9}")
    print(f"\noverall agreement {out['overall_agreement']:.0%}  (gate {out['gate']:.0%})")
    for m in out["misses"]:
        print(f"  miss {m['call_id']:10} {m['rule_id']:22} human={m['human']!s:5} model={m['model']!s:5} [{m['why']}]")
    print(out["verdict"])
    return 0 if out["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="callsense-mcp")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    ev = sub.add_parser("eval", help="score the graded calls and gate on agreement")
    ev.add_argument("--rules-version", type=int)
    ev.add_argument("--rules-file", help="a draft rules YAML to measure without saving it")
    ev.add_argument("--scorer", choices=["baseline", "claude"], default="baseline")
    ev.add_argument("--min-agreement", type=float, default=0.8)
    sub.add_parser("seed", help="rebuild data/scores for the graded calls from their human labels")
    args = parser.parse_args(argv)

    try:
        if args.command in (None, "serve"):
            from .server import serve

            serve()
            return 0
        if args.command == "eval":
            return _eval(args)
        if args.command == "seed":
            seeded = CallSense(Store()).seed_from_labels()
            print(f"seeded {len(seeded)} calls from human labels: {', '.join(seeded)}")
            return 0
    except CallSenseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
