#!/usr/bin/env python3
"""Execute a client's machine-checkable rules against a piece of copy.

This is the deterministic guardrail engine referenced by docs/spec.md §14.2 and
the roadmap's Phase 0 (issue #11). It is both:

  * a library — import ``check_text`` / ``evaluate_rule`` / ``compute_exit`` from
    another script (issue #19's ``check-copy`` operation wraps this module rather
    than reimplementing it); and
  * a CLI — ``python3 scripts/check_rules.py --client <slug> --text "..."``.

It reuses scripts/ontology_loader.py for YAML parsing and file enumeration, so it
parses exactly the same canonical file set the validator gates on. Stdlib only:
no PyYAML, no jsonschema, no third-party packages (Ruby must be on PATH for the
shared loader — see CLAUDE.md).

Contract (issue #11 — #19 inherits it):

  * v1 ``machine_check`` types executed here: ``disallowed_terms``,
    ``required_terms``, ``regex_policy`` (mirrors the schema oneOf in
    schemas/rule.schema.json). Unknown/future types (``approval_required_pattern``,
    ``status_transition``) are skipped, not executed — see EXECUTABLE_TYPES.
  * Matching: case-insensitive substring for the term types; ``re.search`` for
    ``regex_policy``.
  * Enforceable statuses: ``active``, ``approved``, ``prohibited``. A violation
    changes the exit code only when it comes from an enforceable rule whose
    severity meets the ``--fail-on`` threshold (default ``blocking``).
  * Advisory statuses (``draft``, ``proposed``) are reported and labeled
    ``advisory: true`` but never change the exit code, whatever their severity.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# The shared loader lives next to this file; make it importable whether this
# script is run directly (its dir is already on sys.path) or imported by a test
# that inserted scripts/ onto sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ontology_loader import load_documents  # noqa: E402

# Rule statuses whose violations can change the exit code.
ENFORCEABLE_STATUSES = {"active", "approved", "prohibited"}
# Rule statuses that are reported but never affect the exit code.
ADVISORY_STATUSES = {"draft", "proposed"}
# machine_check types this engine can execute in v1. Kept in lockstep with the
# oneOf branches in schemas/rule.schema.json. Future types are deliberately
# absent so an unrecognised (or not-yet-supported) type is skipped, never run.
EXECUTABLE_TYPES = {"disallowed_terms", "required_terms", "regex_policy"}
# --fail-on threshold -> the set of severities that trip a non-zero exit.
FAIL_ON_SEVERITIES = {
    "blocking": {"blocking"},
    "warning": {"warning", "blocking"},
}


class CheckError(Exception):
    """A user/usage error (unknown client, bad text source, missing file)."""


def _rule_matches_pattern(rule_id: Optional[str], patterns: list) -> bool:
    """True if ``rule_id`` matches a projection ``includes.rules`` entry.

    Supports the same ``.*`` suffix wildcard the validator uses for projection
    includes (e.g. ``client.brand.*``).
    """
    if not isinstance(rule_id, str):
        return False
    for pat in patterns or []:
        if not isinstance(pat, str):
            continue
        if pat.endswith(".*"):
            if rule_id.startswith(pat[:-1]):
                return True
        elif rule_id == pat:
            return True
    return False


def select_rules(
    root: Path,
    client_id: str,
    workstream: Optional[str] = None,
    projection_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return the client's rules in scope, each as a plain dict.

    Scope:
      * default — every rule in every module of the client;
      * ``workstream`` — rules from modules whose ``workstreams`` list contains it;
      * ``projection_id`` — rules that the named projection pulls into scope, i.e.
        rules in any module listed under ``includes.modules`` plus rules named (or
        wildcard-matched) under ``includes.rules``.

    ``workstream`` and ``projection_id`` are mutually exclusive (the CLI enforces
    this). Raises CheckError for an unknown client or projection.
    """
    docs = load_documents(root)
    client_ids = {d.get("id") for d in docs.values() if d.get("kind") == "client"}
    if client_id not in client_ids:
        known = ", ".join(sorted(c for c in client_ids if c)) or "(none)"
        raise CheckError(f"unknown client: {client_id!r} (known clients: {known})")

    modules = [
        d
        for d in docs.values()
        if d.get("kind") == "ontology_module" and d.get("client_id") == client_id
    ]

    scoped_modules: Optional[set] = None
    rule_patterns: list = []
    if projection_id is not None:
        projection = next(
            (
                d
                for d in docs.values()
                if d.get("kind") == "projection"
                and d.get("client_id") == client_id
                and d.get("id") == projection_id
            ),
            None,
        )
        if projection is None:
            raise CheckError(
                f"unknown projection for client {client_id!r}: {projection_id!r}"
            )
        includes = projection.get("includes") or {}
        scoped_modules = set(includes.get("modules") or [])
        rule_patterns = includes.get("rules") or []

    selected: list[dict[str, Any]] = []
    for module in modules:
        module_workstreams = module.get("workstreams") or []
        if workstream is not None and workstream not in module_workstreams:
            continue
        for rule in module.get("rules", []) or []:
            if not isinstance(rule, dict):
                continue
            if scoped_modules is not None:
                in_scope = module.get("id") in scoped_modules or _rule_matches_pattern(
                    rule.get("id"), rule_patterns
                )
                if not in_scope:
                    continue
            selected.append(rule)
    return selected


def evaluate_rule(rule: dict[str, Any], text: str) -> Optional[dict[str, Any]]:
    """Run one rule's ``machine_check`` against ``text``.

    Returns a violation dict ``{rule_id, severity, status, matched, statement,
    advisory}`` if the rule is violated, otherwise ``None``. ``matched`` lists the
    concrete triggers: disallowed terms found, required terms *missing*, or the
    regex substring matched. Rules with no ``machine_check`` (or a type this engine
    does not execute) return ``None``.
    """
    machine_check = rule.get("machine_check")
    if not isinstance(machine_check, dict):
        return None
    mc_type = machine_check.get("type")
    if mc_type not in EXECUTABLE_TYPES:
        # Unknown or future (approval_required_pattern / status_transition) type:
        # deterministically skipped in v1, never guessed at.
        return None

    lowered = text.lower()
    matched: list[str] = []
    violated = False

    if mc_type == "disallowed_terms":
        for term in machine_check.get("disallowed_terms") or []:
            if isinstance(term, str) and term.lower() in lowered:
                matched.append(term)
        violated = bool(matched)
    elif mc_type == "required_terms":
        missing = [
            term
            for term in machine_check.get("required_terms") or []
            if isinstance(term, str) and term.lower() not in lowered
        ]
        matched = missing  # the terms responsible for the violation are the missing ones
        violated = bool(missing)
    elif mc_type == "regex_policy":
        pattern = machine_check.get("pattern")
        policy = machine_check.get("policy")
        found = None
        if isinstance(pattern, str):
            try:
                found = re.search(pattern, text)
            except re.error as exc:  # malformed regex is a data bug, surface it
                raise CheckError(
                    f"rule {rule.get('id')!r} has an invalid regex_policy pattern: {exc}"
                )
        if policy == "deny":
            violated = found is not None
            if found is not None:
                matched = [found.group(0)]
        elif policy == "allow":
            violated = found is None
            if found is not None:
                matched = [found.group(0)]

    if not violated:
        return None
    return {
        "rule_id": rule.get("id"),
        "severity": rule.get("severity"),
        "status": rule.get("status"),
        "matched": matched,
        "statement": rule.get("statement"),
        "advisory": rule.get("status") in ADVISORY_STATUSES,
    }


def compute_exit(violations: list[dict[str, Any]], fail_on: str = "blocking") -> int:
    """Return 1 iff any violation is from an enforceable rule whose severity meets
    the ``fail_on`` threshold, else 0. Advisory (draft/proposed) rules never count.
    """
    fail_severities = FAIL_ON_SEVERITIES[fail_on]
    for violation in violations:
        if (
            violation.get("status") in ENFORCEABLE_STATUSES
            and violation.get("severity") in fail_severities
        ):
            return 1
    return 0


def check_text(
    root: Path,
    client_id: str,
    text: str,
    workstream: Optional[str] = None,
    projection_id: Optional[str] = None,
    fail_on: str = "blocking",
) -> tuple[list[dict[str, Any]], int]:
    """Library entry point: select the client's rules, evaluate them against
    ``text``, and return ``(violations, exit_code)``. Violations are sorted by
    rule_id for deterministic output.
    """
    rules = select_rules(root, client_id, workstream, projection_id)
    violations = [v for v in (evaluate_rule(rule, text) for rule in rules) if v]
    violations.sort(key=lambda v: (v.get("rule_id") or ""))
    return violations, compute_exit(violations, fail_on)


def _resolve_text(args: argparse.Namespace, stdin) -> str:
    """Resolve exactly one text source: --text, --file, or stdin.

    Explicit flags take precedence; stdin is read only when neither is given.
    Supplying both --text and --file is an error.
    """
    if args.text is not None and args.file is not None:
        raise CheckError("provide exactly one text source: --text, --file, or stdin")
    if args.text is not None:
        return args.text
    if args.file is not None:
        path = Path(args.file)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckError(f"cannot read --file {args.file!r}: {exc}")
    if hasattr(stdin, "isatty") and stdin.isatty():
        raise CheckError(
            "no text provided: pass --text, --file, or pipe copy on stdin"
        )
    data = stdin.read()
    if not data:
        raise CheckError("no text provided on stdin")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a client's machine-checkable rules against draft copy and report "
            "violations as JSON. Exit is non-zero only for enforceable "
            "(active/approved/prohibited) rules meeting the --fail-on severity."
        )
    )
    parser.add_argument("--client", required=True, help="Client slug, e.g. jmd-menswear")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--workstream", help="Limit to modules carrying this workstream")
    scope.add_argument("--projection", help="Limit to rules in scope for this projection id")
    parser.add_argument("--text", help="Copy to check, inline")
    parser.add_argument("--file", help="Path to a file whose contents are the copy")
    parser.add_argument(
        "--fail-on",
        choices=["blocking", "warning"],
        default="blocking",
        help="Lowest enforceable severity that trips a non-zero exit (default: blocking)",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to load canonical YAML from (default: current dir)",
    )
    return parser


def run(argv: Optional[list] = None, stdin=None) -> int:
    """CLI entry point. Prints the JSON violation list to stdout and returns the
    exit code (0 clean / enforceable-passing, 1 enforceable violation, 2 usage).
    """
    stdin = stdin if stdin is not None else sys.stdin
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        text = _resolve_text(args, stdin)
        violations, exit_code = check_text(
            root,
            args.client,
            text,
            workstream=args.workstream,
            projection_id=args.projection,
            fail_on=args.fail_on,
        )
    except CheckError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(violations, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())
