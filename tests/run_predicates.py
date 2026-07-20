#!/usr/bin/env python3
"""Deterministic predicate-vocabulary sync test.

The controlled predicate enum in schemas/module.schema.json ($defs.predicateName)
is the single source of truth for relationship predicates. This test fails if:

  1. any scripts/validate_ontology.py PREDICATE_CONSTRAINTS key is absent from the
     schema enum (a constraint on a predicate the schema no longer accepts), or
  2. any `inverse` name configured on a live relationship is absent from the enum
     (an inverse that does not name a real vocabulary predicate), or
  3. the bounded `x_` experimental-predicate escape pattern is non-portable — i.e.
     it uses a Python-only regex token (such as `\\Z`) or diverges between the
     repo's Python evaluator and ECMAScript. JSON Schema (Draft 2020-12) `pattern`
     is ECMA-262 syntax, so the pattern must behave identically in both engines
     while still rejecting bare `x_`, embedded whitespace, and trailing newlines.
     Python behaviour and no-Python-only-token are checked unconditionally; the
     ECMAScript parity leg runs through Node when it is on PATH (advisory if not).

Keeping these in lockstep prevents schema/validator drift. The validator also
runs check (1) at runtime; this test additionally scans live inverse usage and
guards the invariants independently in CI.

Run from the repo root:  python3 tests/run_predicates.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import validate_ontology as v  # noqa: E402
from ontology_loader import iter_yaml, parse_yaml  # noqa: E402


def _module_schema() -> dict:
    return json.loads((ROOT / "schemas" / "module.schema.json").read_text(encoding="utf-8"))


def schema_predicate_enum() -> set[str]:
    return set(_module_schema()["$defs"]["predicateName"]["enum"])


def experimental_predicate_pattern() -> str:
    """Return the schema's bounded `x_` experimental-predicate string pattern.

    This is the second `anyOf` branch on `relationship.predicate`; the first is the
    controlled `predicateName` enum `$ref`.
    """
    branches = _module_schema()["$defs"]["relationship"]["properties"]["predicate"]["anyOf"]
    strings = [b["pattern"] for b in branches if b.get("type") == "string" and "pattern" in b]
    if len(strings) != 1:
        raise AssertionError(
            f"expected exactly one string-pattern anyOf branch on relationship.predicate, got {strings!r}"
        )
    return strings[0]


# JSON Schema (Draft 2020-12) `pattern` is ECMAScript/ECMA-262 regex syntax. These
# tokens are Python `re`-only; if any reappears in the experimental-predicate
# pattern the schema silently diverges between the repo's Python evaluator and any
# conformant (ECMAScript) engine — exactly the `\Z` interoperability regression this
# guards against.
PYTHON_ONLY_REGEX_TOKENS = (r"\Z", r"\A", r"(?P<", r"(?P=", r"(?#", r"\Z", "(?i)", "(?s)", "(?m)", "(?x)")

# (input, must_match) — behaviour required identically under Python `re.search`
# (how the validator applies `pattern`) and ECMAScript. `x_fooZ` is the canary: a
# literalized Python-only `\Z` (treated as a literal `Z` in ECMAScript-without-u)
# would wrongly accept it, so it must be rejected by a portable pattern.
PORTABLE_PATTERN_CASES = (
    ("x_held_at", True),
    ("x_amplifies", True),
    ("x_", False),
    ("x_foo bar", False),
    ("x_held_at\n", False),
    ("x_fooZ", False),
)


def check_experimental_predicate_portability() -> list[str]:
    """Fail if the `x_` predicate pattern is non-portable or drifts across engines."""
    failures: list[str] = []
    pattern = experimental_predicate_pattern()

    for token in PYTHON_ONLY_REGEX_TOKENS:
        if token in pattern:
            failures.append(
                f"experimental predicate pattern {pattern!r} contains Python-only regex token "
                f"{token!r}; JSON Schema patterns must be ECMAScript-portable"
            )

    # Python side: exactly how validate_ontology.py applies `pattern` (re.search).
    for text, expected in PORTABLE_PATTERN_CASES:
        got = bool(re.search(pattern, text))
        if got != expected:
            failures.append(
                f"[python] pattern {pattern!r} on {text!r}: got match={got}, expected {expected}"
            )

    # ECMAScript side: run the identical cases through Node when available. Absent
    # Node this stays advisory (the Python + no-token guards above are the
    # deterministic backbone); present Node makes the cross-engine parity binding.
    node = shutil.which("node")
    if not node:
        print("note: node not on PATH; skipping ECMAScript cross-engine parity (advisory)")
        return failures

    payload = json.dumps({"pattern": pattern, "cases": [[t, e] for t, e in PORTABLE_PATTERN_CASES]})
    script = (
        "const {pattern, cases} = JSON.parse(process.argv[1]);"
        "const re = new RegExp(pattern);"          # ECMAScript, no u flag
        "const reU = new RegExp(pattern, 'u');"    # must also compile under u (\\Z would throw here)
        "const out = [];"
        "for (const [t, e] of cases) {"
        " const g = re.test(t);"
        " if (g !== e) out.push(`[node] ${JSON.stringify(t)}: got ${g} expected ${e}`);"
        " if (reU.test(t) !== g) out.push(`[node] u-flag parity mismatch on ${JSON.stringify(t)}`);"
        "}"
        "process.stdout.write(JSON.stringify(out));"
    )
    try:
        proc = subprocess.run(
            [node, "-e", script, payload], capture_output=True, text=True, timeout=30
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        failures.append(f"[node] cross-engine parity check failed to run: {exc}")
        return failures
    if proc.returncode != 0:
        failures.append(f"[node] regex evaluation errored (rc={proc.returncode}): {proc.stderr.strip()}")
        return failures
    try:
        node_failures = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        failures.append(f"[node] unexpected output: {proc.stdout!r}")
        return failures
    failures.extend(node_failures)
    if not node_failures:
        print(f"portable pattern ok under ECMAScript (node): {pattern}")
    return failures


def main() -> int:
    vocab = schema_predicate_enum()
    failures: list[str] = []

    if not vocab:
        failures.append("schemas/module.schema.json $defs.predicateName.enum is empty or missing")

    for key in v.PREDICATE_CONSTRAINTS:
        if key not in vocab:
            failures.append(f"PREDICATE_CONSTRAINTS key {key!r} is absent from the schema predicate enum")

    inverse_count = 0
    for path in iter_yaml(ROOT):
        data = parse_yaml(path)
        if data.get("kind") != "ontology_module":
            continue
        for rel in data.get("relationships", []) or []:
            if not isinstance(rel, dict):
                continue
            inverse = rel.get("inverse")
            if inverse is None:
                continue
            inverse_count += 1
            if inverse not in vocab:
                failures.append(
                    f"{path.relative_to(ROOT)}: relationship {rel.get('id')} inverse {inverse!r} "
                    f"is absent from the schema predicate enum"
                )

    failures.extend(check_experimental_predicate_portability())

    if failures:
        print("PREDICATE SYNC FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1

    print(
        f"predicate sync ok: {len(v.PREDICATE_CONSTRAINTS)} constrained predicate(s) and "
        f"{inverse_count} live inverse name(s) all in the {len(vocab)}-term schema vocabulary; "
        f"experimental `x_` predicate pattern is ECMAScript-portable"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
