#!/usr/bin/env python3
"""Read-only command-line surface over the ontology runtime service (issue #19).

This is the v1 thin adapter over ``scripts/ontology_service.py``: it parses
arguments, loads the chosen backend, calls one transport-agnostic operation, and
prints the plain JSON result. It adds **no** ontology semantics of its own and no
new dependencies (stdlib only; the ``yaml`` backend needs Ruby via the shared
loader, the ``sqlite`` backend does not).

Surface (each maps 1:1 to a service function):

    ontology list-clients
    ontology context     --client <slug> [--projection <id>]
    ontology rules       --client <slug> [--severity <sev>] [--workstream <ws>]
    ontology check-copy  --client <slug> (--text T | --file F | stdin)
                         [--projection <id> | --workstream <ws>] [--fail-on warning]
    ontology projection  --id <projection-id>

Global backend flags on every subcommand:

    --source yaml|sqlite   (default yaml)
    --sqlite-path PATH      (required for --source sqlite)
    --root PATH             (canonical repo root for --source yaml; default .)

Exit codes:
  * ``0``  — success; and a ``check-copy`` with no exit-tripping violation.
  * ``1``  — ONLY ``check-copy``, when an enforceable rule meets ``--fail-on``.
             This is issue #11's exit contract, inherited verbatim.
  * ``2``  — a structured usage/data error (unknown client/projection, an
             unavailable or malformed SQLite backend, a bad argument). Printed as
             ``{"error": "..."}`` on stderr; deterministic.

It is enforcement-surface friendly: ``ontology check-copy ... --file draft.md``
returns non-zero on a blocking violation, so it drops into a consumer repo's CI
step or a pre-publish git hook unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ontology_service as svc  # noqa: E402


class _StructuredArgumentParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose usage errors raise ``ServiceError`` instead of
    printing argparse's free-form usage text and calling ``sys.exit(2)`` directly.

    ``main`` maps ``ServiceError`` to the documented ``{"error": "..."}`` JSON on
    stderr with exit 2, so a malformed argument (unknown flag, missing required
    option, missing subcommand) fails through the SAME deterministic, machine
    -parseable contract as every other error — closing the gap where argparse
    terminated the process before the structured handler could run (Codex Reviewer
    A, Integration Auditor). ``--help`` still exits 0 via the inherited
    ``exit()``/``print_help`` path, which this does not override. Subparsers built
    with ``add_subparsers`` inherit this class, so subcommand errors are structured
    too."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise svc.ServiceError(f"{self.prog}: {message}")


def _add_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=sorted(svc.KNOWN_SOURCES),
        default="yaml",
        help="Read backend: canonical YAML (default) or a prebuilt SQLite export",
    )
    parser.add_argument(
        "--sqlite-path",
        help="Path to the prebuilt SQLite export (required for --source sqlite)",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root for --source yaml (default: current dir)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _StructuredArgumentParser(
        prog="ontology",
        description=(
            "Read-only runtime surface over the client ontologies: list clients, "
            "resolve projection-scoped context, list guardrail rules, check draft "
            "copy against machine_check rules, and resolve a projection. No "
            "create/modify/delete; modeling an operation never grants authority "
            "to run it."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-clients", help="List every client")
    _add_backend_args(p_list)

    p_ctx = sub.add_parser(
        "context", help="Resolve projection-scoped client context (entities + active rules)"
    )
    p_ctx.add_argument("--client", required=True, help="Client slug, e.g. femme-events")
    p_ctx.add_argument(
        "--projection",
        help="Projection id (default: <client>.agent-context)",
    )
    _add_backend_args(p_ctx)

    p_rules = sub.add_parser("rules", help="List a client's guardrail rules")
    p_rules.add_argument("--client", required=True, help="Client slug")
    p_rules.add_argument("--severity", help="Filter to this severity, e.g. blocking")
    p_rules.add_argument("--workstream", help="Filter to modules carrying this workstream")
    _add_backend_args(p_rules)

    p_check = sub.add_parser(
        "check-copy", help="Check draft copy against machine_check rules (enforcement surface)"
    )
    p_check.add_argument("--client", required=True, help="Client slug")
    scope = p_check.add_mutually_exclusive_group()
    scope.add_argument("--workstream", help="Limit to modules carrying this workstream")
    scope.add_argument("--projection", help="Limit to rules in scope for this projection id")
    p_check.add_argument("--text", help="Copy to check, inline")
    p_check.add_argument("--file", help="Path to a file whose contents are the copy")
    p_check.add_argument(
        "--fail-on",
        choices=["blocking", "warning"],
        default="blocking",
        help="Lowest enforceable severity that trips a non-zero exit (default: blocking)",
    )
    _add_backend_args(p_check)

    p_proj = sub.add_parser("projection", help="Resolve a projection slice + provenance")
    p_proj.add_argument("--id", required=True, dest="projection_id", help="Projection id")
    _add_backend_args(p_proj)

    return parser


def _resolve_text(args: argparse.Namespace, stdin) -> str:
    """Resolve exactly one copy source: --text, --file, or stdin (mirrors the
    check_rules CLI contract). Raises ServiceError on a usage mistake."""
    if args.text is not None and args.file is not None:
        raise svc.ServiceError("provide exactly one text source: --text, --file, or stdin")
    if args.text is not None:
        return args.text
    if args.file is not None:
        try:
            return Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise svc.ServiceError(f"cannot read --file {args.file!r}: {exc}")
    if hasattr(stdin, "isatty") and stdin.isatty():
        raise svc.ServiceError("no text provided: pass --text, --file, or pipe copy on stdin")
    data = stdin.read()
    if not data:
        raise svc.ServiceError("no text provided on stdin")
    return data


def _load(args: argparse.Namespace) -> svc.Dataset:
    return svc.load_dataset(
        source=args.source,
        root=getattr(args, "root", None),
        sqlite_path=getattr(args, "sqlite_path", None),
    )


def _dispatch(args: argparse.Namespace, stdin) -> tuple[dict, int]:
    """Run the selected operation; return ``(result_dict, exit_code)``.

    Only ``check-copy`` can return a non-zero exit on success (issue #11's
    contract). All other operations return exit 0. ``ServiceError`` propagates to
    ``main`` for structured reporting."""
    ds = _load(args)
    if args.command == "list-clients":
        return svc.list_clients(ds), 0
    if args.command == "context":
        return svc.get_client_context(ds, args.client, args.projection), 0
    if args.command == "rules":
        return svc.list_rules(ds, args.client, args.severity, args.workstream), 0
    if args.command == "projection":
        return svc.get_projection(ds, args.projection_id), 0
    if args.command == "check-copy":
        text = _resolve_text(args, stdin)
        result = svc.check_copy(
            ds,
            args.client,
            text,
            workstream=args.workstream,
            projection_id=args.projection,
            fail_on=args.fail_on,
        )
        return result, result["exit_code"]
    raise svc.ServiceError(f"unknown command: {args.command!r}")  # pragma: no cover


def main(argv: Optional[list] = None, stdin=None) -> int:
    """CLI entry point (console script ``ontology``). Prints the JSON result to
    stdout and returns the exit code."""
    stdin = stdin if stdin is not None else sys.stdin
    try:
        # parse_args runs INSIDE the handler so an argparse usage error (raised as
        # a ServiceError by _StructuredArgumentParser) is reported through the same
        # structured {"error": ...} exit-2 contract as every other failure.
        args = build_parser().parse_args(argv)
        result, exit_code = _dispatch(args, stdin)
    except svc.ServiceError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


def mcp_entrypoint(argv: Optional[list] = None) -> int:
    """Console entry point ``ontology-mcp`` (registered in pyproject.toml).

    The thin MCP stdio adapter itself is the NEXT PR (``server/ontology_mcp.py``);
    v1 ships the shared core + CLI only. This entry point exists so the packaging
    contract is complete, and fails closed with a deterministic, structured notice
    rather than pretending to serve — no partial/unsafe MCP surface is exposed."""
    print(
        json.dumps(
            {
                "error": "ontology-mcp is not yet implemented",
                "detail": (
                    "The MCP stdio adapter (server/ontology_mcp.py) lands in the "
                    "next PR. v1 ships the shared read-only core "
                    "(scripts/ontology_service.py) and the CLI (`ontology`). Use "
                    "the CLI as the enforcement/CI surface until then."
                ),
                "roadmap": "docs/roadmap.md (Runtime consumer surface shape)",
            }
        ),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
